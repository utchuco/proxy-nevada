import os
import re
import uuid
import base64
import threading
from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, request, send_file, jsonify
from services.storage_service import (
    obter_checklists_pendentes,
    obter_checklist_por_id,
    remover_checklist_pendente,
    salvar_novo_checklist_pendente,
    atualizar_checklist_pendente,
    salvar_pdf_checklist,
    obter_pdf_checklist_path,
    salvar_pdf_pecas_gerado,
    obter_pdf_pecas_gerado_path
)
from services.bluefleet_service import (
    resolver_contract_item_id,
    criar_ocorrencia_e_avancar_fila,
    enriquecer_dados_veiculo
)
from services.ocr_service import extrair_dados_checklist_pdf
from services.pecas_service import (
    extrair_dados_requisicao_pecas,
    processar_calculos_pecas,
    gerar_pdf_requisicao_pecas
)

painel_bp = Blueprint("painel", __name__)

def converter_data_para_iso(data_str):
    """Converte datas nos formatos DD.MM.AA, DD/MM/AAAA, DD-MM-AA, etc. para ISO YYYY-MM-DDTHH:MM:SS."""
    if not data_str:
        return None
    data_str = str(data_str).strip()
    if re.match(r'^\d{4}-\d{2}-\d{2}', data_str):
        if len(data_str) == 10:
            return f"{data_str}T10:00:00"
        return data_str[:19]
    
    m_full = re.search(r'(\d{1,2})[./-](\d{1,2})[./-](\d{4})', data_str)
    if m_full:
        d, m, y = m_full.group(1).zfill(2), m_full.group(2).zfill(2), m_full.group(3)
        return f"{y}-{m}-{d}T10:00:00"

    m_short = re.search(r'(\d{1,2})[./-](\d{1,2})[./-](\d{2})', data_str)
    if m_short:
        d, m, y = m_short.group(1).zfill(2), m_short.group(2).zfill(2), m_short.group(3)
        ano_completo = f"20{y}"
        return f"{ano_completo}-{m}-{d}T10:00:00"
        
    return None

def processar_ocr_assincrono(id_aprovacao, caminho_pdf, dados_iniciais):
    """Executa a decifração da caligrafia com a IA em segundo plano e atualiza os dados do card."""
    try:
        with open(caminho_pdf, "rb") as f:
            pdf_bytes = f.read()

        dados_ia = extrair_dados_checklist_pdf(pdf_bytes) or {}

        placa_informada = (dados_iniciais.get("placa") or "").strip().upper().replace("-", "").replace(" ", "")
        placa_ia = (dados_ia.get("placa") or "").strip().upper().replace("-", "").replace(" ", "")
        placa_final = placa_informada if (placa_informada and "AGUARDANDO" not in placa_informada) else placa_ia

        dados_veiculo = {}
        if placa_final and len(placa_final) == 7:
            dados_veiculo = enriquecer_dados_veiculo(placa_final)

        updates = {
            "status_ocr": "concluido",
            "placa": placa_final or "NÃO IDENTIFICADA",
            "modelo_veiculo": dados_veiculo.get("modelo_veiculo", ""),
            "montadora": dados_veiculo.get("montadora", ""),
            "cliente_nome": dados_veiculo.get("cliente_nome", ""),
            "contrato_id": dados_veiculo.get("contrato_id"),
            "contract_item_id": dados_veiculo.get("contract_item_id")
        }

        if dados_ia.get("km_atual"):
            updates["km_atual"] = dados_ia.get("km_atual")
        if dados_ia.get("nome_solicitante"):
            updates["solicitante"] = dados_ia.get("nome_solicitante")
        if dados_ia.get("telefone_solicitante"):
            updates["telefone"] = dados_ia.get("telefone_solicitante")

        data_ia = converter_data_para_iso(dados_ia.get("data"))
        if data_ia:
            updates["data_agendamento"] = data_ia

        tipo_ia = str(dados_ia.get("tipo") or "").lower().strip()
        if tipo_ia in ["e", "s", "es"]:
            updates["sufixo_tipo"] = tipo_ia

        desc_atual = (dados_iniciais.get("descricao") or "").strip()
        obs_ia = (dados_ia.get("observacoes") or dados_ia.get("avarias_observacoes") or "").strip()

        if obs_ia and not desc_atual:
            updates["descricao"] = obs_ia
        elif obs_ia and desc_atual and desc_atual != "Checklist de veículo":
            updates["descricao"] = f"{desc_atual} ({obs_ia})"
        elif obs_ia:
            updates["descricao"] = obs_ia

        atualizar_checklist_pendente(id_aprovacao, updates)
    except Exception as e:
        print(f"Erro no processamento OCR assíncrono {id_aprovacao}: {e}")
        atualizar_checklist_pendente(id_aprovacao, {
            "status_ocr": "concluido"
        })

@painel_bp.route("/painel-nevada", methods=["GET"])
def painel_nevada():
    pendentes = obter_checklists_pendentes()
    for item in pendentes:
        dt_raw = item.get("data_agendamento")
        if dt_raw:
            try:
                item["data_agendamento_local"] = dt_raw[:16]
            except Exception:
                item["data_agendamento_local"] = datetime.now().strftime("%Y-%m-%dT10:00")
        else:
            item["data_agendamento_local"] = datetime.now().strftime("%Y-%m-%dT10:00")

    return render_template("painel.html", checklists=pendentes)

@painel_bp.route("/painel-nevada/ocr-pdf", methods=["POST"])
def painel_ocr_pdf():
    dados = request.get_json(silent=True) or {}
    pdf_base64 = dados.get("pdf_base64")
    if not pdf_base64:
        return jsonify({"isSuccess": False, "erro": "Arquivo PDF ausente."}), 400

    try:
        pdf_bytes = base64.b64decode(pdf_base64)
        dados_extraidos = extrair_dados_checklist_pdf(pdf_bytes)
        if dados_extraidos:
            return jsonify({"isSuccess": True, "dados": dados_extraidos}), 200
        else:
            return jsonify({"isSuccess": False, "erro": "Não foi possível extrair dados com IA."}), 422
    except Exception as e:
        return jsonify({"isSuccess": False, "erro": str(e)}), 500

@painel_bp.route("/painel-nevada/upload-imediato", methods=["POST"])
def painel_upload_imediato():
    dados = request.get_json(silent=True) or {}
    pdf_base64 = dados.get("pdf_base64")
    if not pdf_base64:
        return jsonify({"isSuccess": False, "erro": "Arquivo PDF é obrigatório."}), 400

    id_aprovacao = str(uuid.uuid4())
    placa_bruta = str(dados.get("placa") or "").upper().strip()
    placa_limpa = placa_bruta.replace("-", "").replace(" ", "")

    pdf_nome = dados.get("pdf_nome") or f"Checklist_{id_aprovacao[:8]}.pdf"
    if not placa_limpa:
        m_placa = re.match(r"^([A-Za-z0-9]{7})-", pdf_nome)
        if m_placa:
            placa_limpa = m_placa.group(1).upper()

    placa_inicial = placa_limpa if placa_limpa else "AGUARDANDO IA..."

    caminho_pdf_salvo = salvar_pdf_checklist(placa_inicial, id_aprovacao, pdf_base64)

    dados_veiculo = {}
    if len(placa_inicial) == 7 and placa_inicial != "AGUARDANDO IA...":
        dados_veiculo = enriquecer_dados_veiculo(placa_inicial)

    hoje_iso = datetime.now().strftime("%Y-%m-%dT10:00:00")
    m_tipo = re.search(r'-(es|e|s)(?:\.pdf)?$', pdf_nome, re.IGNORECASE)
    sufixo_detectado = m_tipo.group(1).lower() if m_tipo else "es"

    novo_pendente = {
        "id_aprovacao": id_aprovacao,
        "placa": placa_inicial,
        "km_atual": dados.get("km_atual") or 0,
        "tipo_veiculo": "alugado",
        "modelo_veiculo": dados_veiculo.get("modelo_veiculo", ""),
        "montadora": dados_veiculo.get("montadora", ""),
        "cliente_nome": dados_veiculo.get("cliente_nome", ""),
        "contrato_id": dados_veiculo.get("contrato_id"),
        "contract_item_id": dados_veiculo.get("contract_item_id"),
        "motivo_id": dados.get("motivo_id") or "CorrectiveMaintenance",
        "motivo_classe": dados.get("motivo_classe") or "CorrectiveMaintenanceMechanicalFailure",
        "plano_revisao_id": "149",
        "descricao": dados.get("descricao") or "",
        "data_agendamento": dados.get("data_agendamento") or hoje_iso,
        "sufixo_tipo": sufixo_detectado,
        "solicitante": dados.get("solicitante") or "Equipe Nevada",
        "telefone": dados.get("telefone") or "11999999999",
        "email": "frota@locadoranevada.com.br",
        "leva_e_traz": False,
        "caminho_pdf": caminho_pdf_salvo,
        "pdf_nome": pdf_nome,
        "data_recebimento": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "status": "Aguardando Aprovação",
        "status_ocr": "processando"
    }

    salvar_novo_checklist_pendente(novo_pendente)

    # Dispara decifração da caligrafia em segundo plano
    t = threading.Thread(
        target=processar_ocr_assincrono,
        args=(id_aprovacao, caminho_pdf_salvo, dados)
    )
    t.daemon = True
    t.start()

    # Se enviou papel de peças junto, processa também em segundo plano
    pecas_b64 = dados.get("pecas_pdf_base64")
    if pecas_b64:
        def _processar_pecas_bg(id_ap, b64_str):
            try:
                p_bytes = base64.b64decode(b64_str)
                d_brutos = extrair_dados_requisicao_pecas(p_bytes)
                if d_brutos:
                    d_proc = processar_calculos_pecas(d_brutos)
                    atualizar_checklist_pendente(id_ap, {"dados_pecas": d_proc})
            except Exception as e_p:
                print(f"Erro no processamento inicial de peças: {e_p}")

        t_pecas = threading.Thread(target=_processar_pecas_bg, args=(id_aprovacao, pecas_b64))
        t_pecas.daemon = True
        t_pecas.start()

    return jsonify({
        "isSuccess": True,
        "id_aprovacao": id_aprovacao,
        "status_ocr": "processando",
        "checklist": novo_pendente
    }), 200

@painel_bp.route("/painel-nevada/status-card/<id_aprovacao>", methods=["GET"])
def obter_status_card(id_aprovacao):
    item = obter_checklist_por_id(id_aprovacao)
    if not item:
        return jsonify({"isSuccess": False, "erro": "Checklist não encontrado."}), 404

    return jsonify({
        "isSuccess": True,
        "status_ocr": item.get("status_ocr", "concluido"),
        "dados": item
    })

@painel_bp.route("/painel-nevada/checklist-pdf/<id_aprovacao>", methods=["GET"])
def ver_pdf_checklist(id_aprovacao):
    caminho_pdf = obter_pdf_checklist_path(id_aprovacao)
    if caminho_pdf:
        try:
            return send_file(caminho_pdf, mimetype="application/pdf")
        except Exception as e:
            return f"Erro ao abrir arquivo PDF: {str(e)}", 500
    return "Checklist ou PDF não encontrado.", 404

@painel_bp.route("/painel-nevada/aprovar/<id_aprovacao>", methods=["POST"])
def painel_aprovar(id_aprovacao):
    checklist_alvo = obter_checklist_por_id(id_aprovacao)
    if not checklist_alvo:
        return jsonify({"isSuccess": False, "erro": "Checklist não encontrado."}), 404

    edicoes = request.get_json(silent=True) or {}

    placa = edicoes.get("placa") or checklist_alvo.get("placa", "").strip().upper()
    km_atual = int(edicoes.get("km_atual") or checklist_alvo.get("km_atual") or 0)
    descricao = edicoes.get("descricao") or checklist_alvo.get("descricao") or "Manutenção"
    solicitante = edicoes.get("solicitante") or checklist_alvo.get("solicitante") or "Equipe Nevada"
    telefone = edicoes.get("telefone") or checklist_alvo.get("telefone") or "11999999999"
    email = edicoes.get("email") or checklist_alvo.get("email") or "frota@locadoranevada.com.br"
    tipo_veiculo = checklist_alvo.get("tipo_veiculo", "alugado")

    placa_limpa = placa.replace("-", "").strip().upper()
    if len(placa_limpa) == 7:
        placa_api = f"{placa_limpa[:3]}-{placa_limpa[3:]}"
    else:
        placa_api = placa

    # 1. Tratamento da Data: formato oficial Blue Fleet (YYYY-MM-DDTHH:MM:SS), respeitando a data real informada
    dt_input = (edicoes.get("data_agendamento") or "").strip()
    if dt_input:
        try:
            if len(dt_input) == 16:
                dt_obj = datetime.strptime(dt_input, "%Y-%m-%dT%H:%M")
                data_iso = dt_obj.strftime("%Y-%m-%dT%H:%M:00")
            elif len(dt_input) >= 19:
                dt_obj = datetime.strptime(dt_input[:19], "%Y-%m-%dT%H:%M:%S")
                data_iso = dt_obj.strftime("%Y-%m-%dT%H:%M:%S")
            else:
                data_iso = dt_input
        except Exception:
            data_iso = datetime.now().strftime("%Y-%m-%dT10:00:00")
    else:
        raw_agendamento = checklist_alvo.get("data_agendamento")
        if raw_agendamento and "T" in str(raw_agendamento):
            data_iso = str(raw_agendamento).replace(".000Z", "").replace("Z", "")
            if len(data_iso) > 19:
                data_iso = data_iso[:19]
            elif len(data_iso) == 16:
                data_iso = f"{data_iso}:00"
        else:
            data_iso = datetime.now().strftime("%Y-%m-%dT10:00:00")

    if not data_iso or len(str(data_iso).strip()) < 10:
        data_iso = datetime.now().strftime("%Y-%m-%dT10:00:00")

    # 2. Mapeamento de motivos e classes da Blue Fleet
    motivo_raw = str(edicoes.get("motivo_id") or checklist_alvo.get("motivo_id", "CorrectiveMaintenance"))
    classe_raw = str(edicoes.get("motivo_classe") or checklist_alvo.get("motivo_classe", "CorrectiveMaintenanceMechanicalFailure"))

    if motivo_raw == "2":
        motivo_id = "CorrectiveMaintenance"
        classe_id = "CorrectiveMaintenanceMechanicalFailure" if classe_raw in ["9", "2", ""] else classe_raw
    elif motivo_raw == "1":
        motivo_id = "PreventiveMaintenance"
        classe_id = "PreventiveMaintenanceQuilometerRevision" if classe_raw in ["1", ""] else classe_raw
    else:
        motivo_id = motivo_raw
        classe_id = classe_raw

    is_revisao = True if motivo_id == "PreventiveMaintenance" else False

    try:
        # 3. Resolução do contractItemId ativo
        contract_item_id = checklist_alvo.get("contract_item_id")
        if not contract_item_id:
            contract_item_id = resolver_contract_item_id(placa_api)

        if not contract_item_id:
            return jsonify({"isSuccess": False, "erro": f"Carro {placa_api} não possui contractItemId ativo na API."}), 400

        # 4. Bloco de Manutenção oficial
        bloco_manutencao = {
            "currentOdometer": km_atual,
            "description": descricao,
            "providerId": 197,
            "city": "São Paulo",
            "state": "SP",
            "country": "BR",
            "latitude": -23.5557714,
            "longitude": -46.6395571,
            "requesterName": solicitante,
            "requesterPhone": telefone,
            "requesterEmail": email,
            "pickupDate": data_iso,
            "scheduling1": data_iso
        }

        # Anexa PDF do checklist se existir fisicamente, garantindo padrão estrito PLACA-DD.MM.YY-TIPO.pdf
        files_payload = []
        caminho_pdf = checklist_alvo.get("caminho_pdf")
        if caminho_pdf and os.path.exists(caminho_pdf):
            try:
                # Extrai formato DD.MM.AA a partir da data informada/editada
                try:
                    dt_parts = data_iso[:10].split("-")
                    data_formatada_pdf = f"{dt_parts[2]}.{dt_parts[1]}.{dt_parts[0][2:]}"
                except Exception:
                    data_formatada_pdf = datetime.now().strftime("%d.%m.%y")

                # Detecta ou usa o tipo escolhido (es, e ou s)
                sufixo_tipo = edicoes.get("sufixo_tipo") or checklist_alvo.get("sufixo_tipo")
                if not sufixo_tipo:
                    nome_original = str(checklist_alvo.get("pdf_nome") or "")
                    match_tipo = re.search(r'-(es|e|s)(?:\.pdf)?$', nome_original, re.IGNORECASE)
                    sufixo_tipo = match_tipo.group(1).lower() if match_tipo else "es"
                else:
                    sufixo_tipo = str(sufixo_tipo).lower().strip()

                nome_arquivo_final = f"{placa_limpa}-{data_formatada_pdf}-{sufixo_tipo}.pdf"

                with open(caminho_pdf, "rb") as f_pdf:
                    b64_pdf = base64.b64encode(f_pdf.read()).decode()
                files_payload.append({
                    "fileName": nome_arquivo_final,
                    "base64Contents": b64_pdf,
                    "fileType": "UnidentifiedDocument"
                })
            except Exception as e_pdf:
                print(f"Aviso: não foi possível codificar PDF do checklist: {e_pdf}")
        elif caminho_pdf:
            print(f"Aviso: Arquivo PDF indicado não encontrado no disco: {caminho_pdf}")
        else:
            print(f"Aviso: Checklist {id_aprovacao} aprovado sem arquivo PDF vinculado.")

        # Se houver Requisição de Peças gerada, inclui automaticamente nos arquivos da ocorrência
        caminho_pdf_pecas, nome_pecas = obter_pdf_pecas_gerado_path(id_aprovacao)
        if caminho_pdf_pecas and os.path.exists(caminho_pdf_pecas):
            try:
                with open(caminho_pdf_pecas, "rb") as f_pecas:
                    b64_pecas = base64.b64encode(f_pecas.read()).decode()
                files_payload.append({
                    "fileName": nome_pecas or "Requisicao_Pecas.pdf",
                    "base64Contents": b64_pecas,
                    "fileType": "UnidentifiedDocument"
                })
            except Exception as e_pec:
                print(f"Aviso: falha ao codificar PDF de peças: {e_pec}")

        payload_bluefleet = {
            "contractItemRequestReasonId": motivo_id,
            "contractItemRequestReasonClassId": classe_id,
            "maintenance": bloco_manutencao,
            "files": files_payload
        }

        if tipo_veiculo == "alugado":
            payload_bluefleet["updateContractItemDriver"] = False

        # Leva e Traz: apenas inclui no payload se o usuário selecionou explicitamente Sim ou Não
        leva_traz_opcao = edicoes.get("leva_e_traz")
        if leva_traz_opcao in ["true", True]:
            payload_bluefleet["hasPickUpReturnService"] = True
        elif leva_traz_opcao in ["false", False]:
            payload_bluefleet["hasPickUpReturnService"] = False
        # Se for vazio ("") ou None, não envia o campo (comportamento padrão Blue Fleet)

        if is_revisao:
            payload_bluefleet["hasDefaultMaintenancePlan"] = True
            payload_bluefleet["maintenancePlanRevisionId"] = "149"

        # 5. Envio e avanço automático da fila na Blue Fleet
        sucesso, req_id, erro_bf = criar_ocorrencia_e_avancar_fila(contract_item_id, payload_bluefleet, data_iso)

        if sucesso:
            remover_checklist_pendente(id_aprovacao)
            return jsonify({
                "isSuccess": True,
                "mensagem": f"Ocorrência criada com sucesso para a placa {placa_api}!",
                "contractItemRequestId": req_id
            }), 200
        else:
            return jsonify({"isSuccess": False, "erro": f"Blue Fleet recusou: {erro_bf}"}), 400

    except Exception as e:
        return jsonify({"isSuccess": False, "erro": f"Erro interno ao disparar: {str(e)}"}), 500

@painel_bp.route("/painel-nevada/descartar/<id_aprovacao>", methods=["POST"])
@painel_bp.route("/painel-nevada/rejeitar/<id_aprovacao>", methods=["POST"])
def painel_rejeitar(id_aprovacao):
    remover_checklist_pendente(id_aprovacao)
    return jsonify({"isSuccess": True, "mensagem": "Checklist descartado com sucesso."}), 200

@painel_bp.route("/painel-nevada/upload-pecas/<id_aprovacao>", methods=["POST"])
def painel_upload_pecas(id_aprovacao):
    dados = request.get_json(silent=True) or {}
    pdf_base64 = dados.get("pdf_base64")
    if not pdf_base64:
        return jsonify({"isSuccess": False, "erro": "Arquivo da requisição de peças ausente."}), 400

    try:
        if "," in pdf_base64:
            pdf_base64 = pdf_base64.split(",")[1]
        pdf_bytes = base64.b64decode(pdf_base64)

        # 1. Extração com IA (Gemini 3)
        dados_brutos = extrair_dados_requisicao_pecas(pdf_bytes)
        if not dados_brutos:
            return jsonify({"isSuccess": False, "erro": "Não foi possível extrair os dados da requisição com IA."}), 422

        # 2. Cálculos matemáticos exatos no Python
        dados_processados = processar_calculos_pecas(dados_brutos)

        # 3. Mescla dados existentes do checklist se a requisição não tiver algum campo
        chk = obter_checklist_por_id(id_aprovacao)
        if chk:
            if not dados_processados.get("placa") or len(str(dados_processados.get("placa"))) < 7:
                dados_processados["placa"] = chk.get("placa", "")
            if not dados_processados.get("requisitante"):
                dados_processados["requisitante"] = chk.get("cliente_nome") or chk.get("solicitante", "")
            if not dados_processados.get("km"):
                dados_processados["km"] = chk.get("km_atual", "")
            if not dados_processados.get("veiculo"):
                dados_processados["veiculo"] = chk.get("modelo_veiculo", "")

        # 4. Salva no checklist pendente
        atualizar_checklist_pendente(id_aprovacao, {
            "dados_pecas": dados_processados
        })

        return jsonify({
            "isSuccess": True,
            "dados_pecas": dados_processados
        }), 200
    except Exception as e:
        print(f"Erro ao processar requisição de peças ({id_aprovacao}): {e}")
        return jsonify({"isSuccess": False, "erro": str(e)}), 500

@painel_bp.route("/painel-nevada/gerar-pdf-pecas/<id_aprovacao>", methods=["POST"])
def painel_gerar_pdf_pecas(id_aprovacao):
    dados_req = request.get_json(silent=True) or {}
    chk = obter_checklist_por_id(id_aprovacao)
    if not chk:
        return jsonify({"isSuccess": False, "erro": "Checklist não encontrado."}), 404

    dados_pecas = chk.get("dados_pecas") or {}
    # Atualiza dados informados na tela (ex: número da OC digitado pelo usuário)
    if dados_req.get("oc_num"):
        dados_pecas["oc_num"] = str(dados_req.get("oc_num")).strip()
    if dados_req.get("data"):
        dados_pecas["data"] = str(dados_req.get("data")).strip()
    if dados_req.get("placa"):
        dados_pecas["placa"] = str(dados_req.get("placa")).strip()
    if dados_req.get("km"):
        dados_pecas["km"] = str(dados_req.get("km")).strip()
    if dados_req.get("veiculo"):
        dados_pecas["veiculo"] = str(dados_req.get("veiculo")).strip()
    if dados_req.get("requisitante"):
        dados_pecas["requisitante"] = str(dados_req.get("requisitante")).strip()

    try:
        # Gera o PDF limpo
        pdf_bytes = gerar_pdf_requisicao_pecas(dados_pecas)
        oc_salvar = dados_pecas.get("oc_num") or f"121471_{chk.get('placa', '')}"
        caminho_salvo, nome_arquivo = salvar_pdf_pecas_gerado(id_aprovacao, oc_salvar, pdf_bytes)

        dados_pecas["caminho_pdf_gerado"] = caminho_salvo
        dados_pecas["nome_pdf_gerado"] = nome_arquivo

        atualizar_checklist_pendente(id_aprovacao, {
            "dados_pecas": dados_pecas
        })

        return jsonify({
            "isSuccess": True,
            "nome_arquivo": nome_arquivo,
            "url_download": f"/painel-nevada/download-pdf-pecas/{id_aprovacao}"
        }), 200
    except Exception as e:
        print(f"Erro ao gerar PDF da requisição de peças: {e}")
        return jsonify({"isSuccess": False, "erro": str(e)}), 500

@painel_bp.route("/painel-nevada/download-pdf-pecas/<id_aprovacao>", methods=["GET"])
def painel_download_pdf_pecas(id_aprovacao):
    caminho_pdf, nome_arquivo = obter_pdf_pecas_gerado_path(id_aprovacao)
    if caminho_pdf and os.path.exists(caminho_pdf):
        return send_file(
            caminho_pdf,
            mimetype="application/pdf",
            as_attachment=False,
            download_name=nome_arquivo or "Requisicao_Pecas.pdf"
        )
    return jsonify({"isSuccess": False, "erro": "PDF da requisição de peças não encontrado."}), 404

