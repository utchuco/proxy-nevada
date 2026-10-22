import os
import base64
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, send_file, jsonify
from services.storage_service import (
    obter_checklists_pendentes,
    obter_checklist_por_id,
    remover_checklist_pendente,
    obter_pdf_checklist_path
)
from services.bluefleet_service import (
    resolver_contract_item_id,
    criar_ocorrencia_e_avancar_fila
)
from services.ocr_service import extrair_dados_checklist_pdf

painel_bp = Blueprint("painel", __name__)

@painel_bp.route("/painel-nevada", methods=["GET"])
def painel_nevada():
    pendentes = obter_checklists_pendentes()
    for item in pendentes:
        dt_raw = item.get("data_agendamento")
        if dt_raw:
            try:
                item["data_agendamento_local"] = dt_raw[:16]
            except Exception:
                item["data_agendamento_local"] = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT10:00")
        else:
            item["data_agendamento_local"] = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT10:00")

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

    # 1. Tratamento da Data: formato oficial Blue Fleet (YYYY-MM-DDTHH:MM:SS) e futura
    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT10:00:00")

    dt_input = (edicoes.get("data_agendamento") or "").strip()
    if dt_input:
        try:
            dt_obj = datetime.strptime(dt_input, "%Y-%m-%dT%H:%M")
            if dt_obj <= datetime.now():
                dt_obj = datetime.now() + timedelta(days=1)
            data_iso = dt_obj.strftime("%Y-%m-%dT%H:%M:00")
        except Exception:
            data_iso = amanha
    else:
        raw_agendamento = checklist_alvo.get("data_agendamento")
        if raw_agendamento and "T" in str(raw_agendamento):
            data_iso = str(raw_agendamento).replace(".000Z", "").replace("Z", "")
            if len(data_iso) > 19:
                data_iso = data_iso[:19]
            try:
                dt_teste = datetime.strptime(data_iso, "%Y-%m-%dT%H:%M:%S")
                if dt_teste <= datetime.now():
                    data_iso = amanha
            except Exception:
                data_iso = amanha
        else:
            data_iso = amanha

    if not data_iso or len(str(data_iso).strip()) < 10:
        data_iso = amanha

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

                # Detecta o tipo (e, s ou es) a partir do nome original
                nome_original = str(checklist_alvo.get("pdf_nome") or "")
                match_tipo = re.search(r'-(es|e|s)(?:\.pdf)?$', nome_original, re.IGNORECASE)
                sufixo_tipo = match_tipo.group(1).lower() if match_tipo else "e"

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

