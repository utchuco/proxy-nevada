import os
import json
import base64
import requests
import re 
from datetime import datetime, timezone, timedelta
import uuid
from flask import Flask, render_template, request, send_file, jsonify, Response
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri="memory://"
)

CLIENT_ID = os.getenv("BLUEFLEET_CLIENT_ID")
CLIENT_SECRET = os.getenv("BLUEFLEET_CLIENT_SECRET")
AUTH_URL = "https://auth.bluefleet.com.br/connect/token"
API_URL = "https://api.bluefleet.com.br"

def get_access_token():
    if not CLIENT_ID or not CLIENT_SECRET:
        raise ValueError("Credenciais ausentes no servidor (verifique o arquivo .env)")
    credentials = f"{CLIENT_ID}:{CLIENT_SECRET}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    headers = {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {"grant_type": "client_credentials"}
    response = requests.post(AUTH_URL, headers=headers, data=data)
    response.raise_for_status()
    return response.json().get("access_token")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/buscar", methods=["POST"])
def buscar():
    placa = request.form.get("placa", "").strip().upper()
    if not placa:
        return render_template("index.html", erro="Por favor, digite uma placa válida.")

    if len(placa) == 7 and "-" not in placa:
        placa = f"{placa[:3]}-{placa[3:]}"

    try:
        token = get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        
        import time
        
        def get_blindado(url, rapido=False):
            tentativas = 1 if rapido else 3
            tempo_limite = 3 if rapido else 5
            
            for tentativa in range(tentativas):
                try:
                    resp = requests.get(url, headers=headers, timeout=tempo_limite)
                    if resp.status_code == 200:
                        return resp
                    elif resp.status_code == 429:
                        time.sleep(1)
                except Exception:
                    pass
                if not rapido:
                    time.sleep(0.5)
            
            try:
                return requests.get(url, headers=headers, timeout=tempo_limite)
            except Exception:
                resp_erro = requests.Response()
                resp_erro.status_code = 500
                return resp_erro

        url_busca = f"{API_URL}/vehicle?LicensePlate={placa}"
        response = get_blindado(url_busca)
        
        if response.status_code == 429:
            return render_template("index.html", erro="A Blue Fleet limitou nossas buscas por excesso de requisições. Aguarde 1 minutinho e tente novamente.")
            
        response.raise_for_status()
        
        lista_veiculos = response.json().get("data", [])
        if not lista_veiculos:
            return render_template("index.html", erro=f"Veículo não encontrado para a placa {placa}.")
        
        veiculo_base = lista_veiculos[0]
        veiculo_id = veiculo_base.get("vehicleId")
        
        carro_titular = None
        carro_reserva = None
        reserva_oculta_detectada = False
        placa_limpa_base = placa.replace("-", "")

        # ==========================================
        # LÓGICA 1: PESQUISOU O RESERVA
        # ==========================================
        if str(veiculo_base.get("vehicleStatusId")) == "14":
            carro_reserva = veiculo_base
            
            try:
                r_ocorrencia = get_blindado(f"{API_URL}/contract-item-request/search?LicensePlate={placa}")
                if r_ocorrencia.status_code == 200:
                    ocorrencias = r_ocorrencia.json().get("data", [])
                    if ocorrencias:
                        ocorrencias.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
                        for oc in ocorrencias:
                            placa_titular = oc.get("licensePlate")
                            
                            if placa_titular and placa_titular.replace("-", "").upper() != placa_limpa_base:
                                r_titular = get_blindado(f"{API_URL}/vehicle?LicensePlate={placa_titular}")
                                if r_titular.status_code == 200 and r_titular.json().get("data"):
                                    carro_titular = r_titular.json().get("data")[0]
                                    break
            except Exception:
                pass

        # ==========================================
        # LÓGICA 2: PESQUISOU O TITULAR
        # ==========================================
        else:
            carro_titular = veiculo_base
            
            try:
                r_ocorrencia = get_blindado(f"{API_URL}/contract-item-request/search?LicensePlate={placa}")
                if r_ocorrencia.status_code == 200:
                    ocorrencias = r_ocorrencia.json().get("data", [])
                    ocorrencias.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
                    
                    for oc in ocorrencias[:10]:
                        time.sleep(0.1)
                        req_id = oc.get("contractItemRequestId")
                        if not req_id: continue
                        
                        r_files = get_blindado(f"{API_URL}/contract-item-request/{req_id}/files", rapido=True)
                        if r_files.status_code == 200:
                            arquivos = r_files.json().get("data", r_files.json())
                            
                            if isinstance(arquivos, list):
                                for arquivo in arquivos:
                                    nome_bruto = str(arquivo.get("filename", arquivo.get("fileName", arquivo.get("name", "")))).upper()
                                    
                                    match_placa = re.search(r'([A-Z]{3}[ -]?[0-9][A-Z0-9][0-9]{2})', nome_bruto)
                                    
                                    if match_placa:
                                        placa_achada = match_placa.group(1).replace("-", "").replace(" ", "")
                                        
                                        if placa_achada != placa_limpa_base:
                                            placa_reserva_api = f"{placa_achada[:3]}-{placa_achada[3:]}"
                                            
                                            r_reserva = get_blindado(f"{API_URL}/vehicle?LicensePlate={placa_reserva_api}")
                                            if r_reserva.status_code == 200 and r_reserva.json().get("data"):
                                                v_teste = r_reserva.json().get("data")[0]
                                                
                                                if str(v_teste.get("vehicleStatusId")) == "14":
                                                    carro_reserva = v_teste
                                                    reserva_oculta_detectada = False
                                                    break 
                                                    
                        if carro_reserva:
                            break 
                            
            except Exception as e:
                print(f"Erro na extração do reserva: {e}")
                pass
                
            if not carro_reserva:
                try:
                    if 'r_ocorrencia' in locals() and r_ocorrencia.status_code == 200:
                        for oc in ocorrencias[:5]:
                            if "RESERVA" in json.dumps(oc).upper() and "AGUARDANDO DEVOLU" in json.dumps(oc).upper():
                                reserva_oculta_detectada = True
                                break
                except Exception:
                    pass

        return render_template("resultado.html", titular=carro_titular, reserva=carro_reserva, aviso_reserva=reserva_oculta_detectada)

    except requests.exceptions.HTTPError as err_http:
        return render_template("index.html", erro=f"Falha na comunicação com a API: {err_http}")
    except Exception as e:
        return render_template("index.html", erro=f"Erro interno do sistema: {str(e)}")


@app.route("/checklist/<placa>")
def buscar_checklist(placa):
    placa = placa.strip().upper()
    placa_limpa = placa.replace("-", "")
    
    if len(placa) == 7 and "-" not in placa:
        placa = f"{placa[:3]}-{placa[3:]}"

    try:
        token = get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        
        r_ocorrencia = requests.get(f"{API_URL}/contract-item-request/search?LicensePlate={placa}", headers=headers)
        if r_ocorrencia.status_code != 200:
            return "Erro ao buscar histórico de ocorrências na Blue Fleet.", 500
            
        ocorrencias = r_ocorrencia.json().get("data", [])
        if not ocorrencias:
            return "Nenhuma ocorrência encontrada para este veículo.", 404
            
        ocorrencias.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        
        for oc in ocorrencias:
            req_id = oc.get("contractItemRequestId")
            if not req_id: continue
                
            url_arquivos = f"{API_URL}/contract-item-request/{req_id}/files"
            r_files = requests.get(url_arquivos, headers=headers)
            
            if r_files.status_code == 200:
                resposta_arquivos = r_files.json()
                lista_arquivos = resposta_arquivos.get("data", resposta_arquivos) 
                
                if isinstance(lista_arquivos, list):
                    for arquivo in lista_arquivos:
                        nome_bruto = str(arquivo.get("filename", arquivo.get("fileName", arquivo.get("name", "")))).upper()
                        
                        padrao_checklist = rf"^{placa_limpa}-\d{{2}}\.\d{{2}}\.\d{{2}}"
                        
                        if re.search(padrao_checklist, nome_bruto) and placa_limpa != "":
                            id_arquivo = arquivo.get("id", arquivo.get("fileId"))
                            url_direta = arquivo.get("url")
                            
                            if url_direta:
                                r_pdf = requests.get(url_direta)
                            else:
                                url_api = f"{API_URL}/contract-item-request/{req_id}/files/{id_arquivo}"
                                r_pdf = requests.get(url_api, headers=headers)
                            
                            if r_pdf.status_code == 200:
                                return Response(
                                    r_pdf.content,
                                    mimetype='application/pdf', 
                                    headers={"Content-Disposition": f"inline; filename=Checklist_{placa_limpa}.pdf"}
                                )
                                
        return f"Checklist não encontrado para a placa {placa}. Verifique se o PDF está anexado nas últimas ocorrências.", 404

    except Exception as e:
        return f"Erro interno ao buscar checklist: {str(e)}", 500


@app.route('/crlv', methods=['POST'])
@limiter.limit("5 per minute")
def acessar_crlv():
    placa = request.form.get('placa')
    senha = request.form.get('senha')
    
    senha_segura = os.getenv("PIN_CRLV", "BloqueioEmergenciaNevada2026")
    
    if senha != senha_segura:
        return "Acesso negado: Senha incorreta ou limite de tentativas excedido.", 403
    
    placa_limpa = placa.replace('-', '').replace(' ', '').upper()
    pastas_busca = ['documentos/2026', 'documentos/2025']
    
    for pasta in pastas_busca:
        if os.path.exists(pasta):
            for raiz, subpastas, arquivos in os.walk(pasta):
                for arquivo in arquivos:
                    nome_arquivo_limpo = arquivo.replace('-', '').replace(' ', '').upper()
                    
                    if placa_limpa in nome_arquivo_limpo and arquivo.upper().endswith('.PDF'):
                        caminho_completo = os.path.join(raiz, arquivo)
                        return send_file(caminho_completo, mimetype='application/pdf')
    
    return f"Documento não encontrado para a placa {placa}.", 404

@app.route("/api/receber-checklist", methods=["POST"])
def receber_checklist():
    dados = request.get_json()
    
    if not dados or not (dados.get("placa") or dados.get("placa_veiculo")):
        return jsonify({"isSuccess": False, "erro": "Dados ou placa ausentes."}), 400

    placa_bruta = str(dados.get("placa") or dados.get("placa_veiculo")).upper().strip()
    placa_limpa = placa_bruta.replace("-", "").replace(" ", "")
    
    # Define data futura sugerida padrão (amanhã às 10:00) se não for informada
    amanha_iso = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT10:00:00")
    data_agendamento = dados.get("data_agendamento") or dados.get("data_sugerida") or amanha_iso

    # Tratamento do tipo de ocorrência
    tipo_input = str(dados.get("tipo_manutencao") or "").lower()
    motivo_id = dados.get("motivo_id")
    motivo_classe = dados.get("motivo_classe")
    
    if not motivo_id:
        if "prev" in tipo_input or "revis" in tipo_input:
            motivo_id = "PreventiveMaintenance"
            motivo_classe = motivo_classe or "PreventiveMaintenanceQuilometerRevision"
        else:
            motivo_id = "CorrectiveMaintenance"
            motivo_classe = motivo_classe or "CorrectiveMaintenanceMechanicalFailure"

    id_aprovacao = str(uuid.uuid4())
    
    # Processamento do PDF do Checklist (Base64)
    caminho_pdf_salvo = None
    pdf_base64 = dados.get("pdf_base64")
    pdf_nome = dados.get("pdf_nome") or f"Checklist_{placa_limpa}.pdf"
    
    if pdf_base64:
        try:
            pasta_checklists = os.path.join(os.path.dirname(__file__), 'documentos', 'checklists_recebidos')
            os.makedirs(pasta_checklists, exist_ok=True)
            nome_arquivo_seguro = f"Checklist_{placa_limpa}_{id_aprovacao[:8]}.pdf"
            caminho_completo_pdf = os.path.join(pasta_checklists, nome_arquivo_seguro)
            
            if "," in pdf_base64:
                pdf_base64 = pdf_base64.split(",")[1]
                
            with open(caminho_completo_pdf, "wb") as f_pdf:
                f_pdf.write(base64.b64decode(pdf_base64))
            caminho_pdf_salvo = caminho_completo_pdf
        except Exception as e_pdf:
            print(f"Aviso: Erro ao salvar PDF do checklist: {e_pdf}")

    # Enriquecimento automático com dados da Blue Fleet (Modelo, Cliente, Contrato)
    modelo_veiculo = ""
    montadora = ""
    cliente_nome = ""
    contrato_id = None
    contract_item_id = None

    try:
        token = get_access_token()
        headers_bf = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        placa_consulta = f"{placa_limpa[:3]}-{placa_limpa[3:]}" if len(placa_limpa) == 7 else placa_limpa

        r_veic = requests.get(f"{API_URL}/vehicle?LicensePlate={placa_consulta}", headers=headers_bf, timeout=5)
        if r_veic.status_code == 200 and r_veic.json().get("data"):
            veic = r_veic.json()["data"][0]
            modelo_veiculo = veic.get("vehicleModelName", "")
            montadora = veic.get("automakerName", "")
            veiculo_id = veic.get("vehicleId")

            contrato = veic.get("contract") or {}
            contrato_id = contrato.get("contractId")
            cliente_nome = (contrato.get("customer") or {}).get("tradingName", "")

            # Se possui contrato ativo, já descobre o contractItemId de imediato
            if contrato_id:
                r_items = requests.get(f"{API_URL}/contract/{contrato_id}/items", headers=headers_bf, timeout=5)
                if r_items.status_code == 200:
                    itens = r_items.json().get("data", r_items.json())
                    if isinstance(itens, list):
                        for it in itens:
                            if it.get("vehicleId") == veiculo_id:
                                contract_item_id = it.get("contractItemId") or it.get("id")
                                break
    except Exception as e_enrich:
        print(f"Aviso: Não foi possível enriquecer dados da placa {placa_limpa}: {e_enrich}")

    novo_pendente = {
        "id_aprovacao": id_aprovacao,
        "placa": placa_limpa,
        "km_atual": dados.get("km_atual") or dados.get("km") or 0,
        "tipo_veiculo": dados.get("tipo_veiculo", "alugado").lower(),
        "modelo_veiculo": modelo_veiculo,
        "montadora": montadora,
        "cliente_nome": cliente_nome,
        "contrato_id": contrato_id,
        "contract_item_id": contract_item_id,
        "motivo_id": str(motivo_id),
        "motivo_classe": str(motivo_classe),
        "plano_revisao_id": str(dados.get("plano_revisao_id", "149")),
        "descricao": dados.get("descricao") or dados.get("defeito_relatado") or "Manutenção",
        "data_agendamento": data_agendamento,
        "solicitante": dados.get("solicitante") or dados.get("motorista_nome") or "Equipe Nevada",
        "telefone": dados.get("telefone") or dados.get("motorista_telefone") or "11999999999",
        "email": dados.get("email") or dados.get("motorista_email") or "frota@locadoranevada.com.br",
        "leva_e_traz": bool(dados.get("leva_e_traz", False)),
        "caminho_pdf": caminho_pdf_salvo,
        "pdf_nome": pdf_nome,
        "data_recebimento": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "status": "Aguardando Aprovação"
    }
    
    caminho_pendentes = os.path.join(os.path.dirname(__file__), 'checklists_pendentes.json')
    
    fila_pendentes = []
    if os.path.exists(caminho_pendentes):
        try:
            with open(caminho_pendentes, 'r', encoding='utf-8') as f:
                fila_pendentes = json.load(f)
        except Exception:
            pass
            
    fila_pendentes.append(novo_pendente)
    
    with open(caminho_pendentes, 'w', encoding='utf-8') as f:
        json.dump(fila_pendentes, f, ensure_ascii=False, indent=4)
        
    return jsonify({
        "isSuccess": True, 
        "mensagem": "Checklist completo recebido e guardado para aprovação!",
        "id_aprovacao": novo_pendente["id_aprovacao"]
    }), 200

@app.route("/painel/checklist-pdf/<id_aprovacao>", methods=["GET"])
def ver_pdf_checklist(id_aprovacao):
    caminho_pendentes = os.path.join(os.path.dirname(__file__), 'checklists_pendentes.json')
    if os.path.exists(caminho_pendentes):
        try:
            with open(caminho_pendentes, 'r', encoding='utf-8') as f:
                pendentes = json.load(f)
            for item in pendentes:
                if item.get("id_aprovacao") == id_aprovacao and item.get("caminho_pdf"):
                    caminho_pdf = item.get("caminho_pdf")
                    if os.path.exists(caminho_pdf):
                        return send_file(caminho_pdf, mimetype='application/pdf')
        except Exception as e:
            return f"Erro ao abrir arquivo PDF: {str(e)}", 500
    return "Checklist ou PDF não encontrado.", 404

@app.route("/painel", methods=["GET"])
@app.route("/painel-nevada", methods=["GET"])
def painel_nevada():
    caminho_pendentes = os.path.join(os.path.dirname(__file__), 'checklists_pendentes.json')
    pendentes = []
    
    if os.path.exists(caminho_pendentes):
        try:
            with open(caminho_pendentes, 'r', encoding='utf-8') as f:
                pendentes = json.load(f)
        except Exception:
            pass
            
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

@app.route("/painel/aprovar/<id_aprovacao>", methods=["POST"])
@app.route("/painel-nevada/aprovar/<id_aprovacao>", methods=["POST"])
def painel_aprovar(id_aprovacao):
    caminho_pendentes = os.path.join(os.path.dirname(__file__), 'checklists_pendentes.json')
    if not os.path.exists(caminho_pendentes):
        return jsonify({"isSuccess": False, "erro": "Nenhum checklist pendente."}), 404
        
    try:
        with open(caminho_pendentes, 'r', encoding='utf-8') as f:
            pendentes = json.load(f)
    except Exception:
        return jsonify({"isSuccess": False, "erro": "Erro ao ler arquivo."}), 500
        
    checklist_alvo = None
    nova_fila = []
    for item in pendentes:
        if item.get("id_aprovacao") == id_aprovacao:
            checklist_alvo = item
        else:
            nova_fila.append(item)
            
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
    
    # 1. Tratamento da Data: O formato EXATO oficial da Blue Fleet (SEM Z e sem milissegundos)
    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT10:00:00")
    
    dt_input = (edicoes.get("data_agendamento") or "").strip()
    if dt_input:
        try:
            dt_obj = datetime.strptime(dt_input, "%Y-%m-%dT%H:%M")
            # Garante que seja sempre data futura
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
                data_iso = data_iso[:19] # Corta nos segundos exatos
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

    # 2. Mapeamento Exato do Swagger (Textos oficiais em vez de números)
    motivo_raw = str(edicoes.get("motivo_id") or checklist_alvo.get("motivo_id", "CorrectiveMaintenance"))
    classe_raw = str(edicoes.get("motivo_classe") or checklist_alvo.get("motivo_classe", "CorrectiveMaintenanceMechanicalFailure"))

    # Trava de compatibilidade: Se vier número ("1" ou "2"), o backend traduz para o enum oficial
    if motivo_raw == "2":
        motivo_id = "CorrectiveMaintenance"
        classe_id = "CorrectiveMaintenanceMechanicalFailure" if classe_raw in ["9", "2", ""] else classe_raw
    elif motivo_raw == "1":
        motivo_id = "PreventiveMaintenance"
        classe_id = "PreventiveMaintenanceQuilometerRevision" if classe_raw in ["1", ""] else classe_raw
    else:
        motivo_id = motivo_raw
        classe_id = classe_raw

    # Define se é revisão para enviar os campos extras de quilometragem
    is_revisao = True if motivo_id == "PreventiveMaintenance" else False
    
    try:
        token = get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        # 3. Resolução infalível do ContractItemId ATUAL (Prioriza sempre o contrato ativo)
        contract_item_id = checklist_alvo.get("contract_item_id")
        
        # Se não foi pré-resolvido na chegada do checklist, descobre agora
        if not contract_item_id:
            # Estratégia 1: Busca o contrato ativo vinculado diretamente ao veículo
            r_veic = requests.get(f"{API_URL}/vehicle?LicensePlate={placa_api}", headers=headers)
            if r_veic.status_code == 200:
                veiculos = r_veic.json().get("data", [])
                if veiculos:
                    contrato = veiculos[0].get("contract") or {}
                    contrato_id = contrato.get("contractId")
                    veiculo_id = veiculos[0].get("vehicleId")
                    
                    if contrato_id:
                        r_items = requests.get(f"{API_URL}/contract/{contrato_id}/items", headers=headers)
                        if r_items.status_code == 200:
                            itens = r_items.json().get("data", r_items.json())
                            if isinstance(itens, list):
                                for item_c in itens:
                                    if item_c.get("vehicleId") == veiculo_id:
                                        contract_item_id = item_c.get("contractItemId") or item_c.get("id")
                                        break
                                if not contract_item_id and itens:
                                    contract_item_id = itens[0].get("contractItemId") or itens[0].get("id")

        # Estratégia 2: Busca por itens de contrato ativos da placa (/contract-item/search)
        if not contract_item_id:
            r_ci = requests.get(f"{API_URL}/contract-item/search?LicensePlate={placa_api}", headers=headers)
            if r_ci.status_code == 200:
                itens_ci = r_ci.json().get("data", [])
                for it in itens_ci:
                    status_nome = str(it.get("contractItemStatusName", "")).lower()
                    if "andamento" in status_nome or str(it.get("contractItemStatusId")) == "1":
                        contract_item_id = it.get("contractItemId")
                        break
                if not contract_item_id and itens_ci:
                    contract_item_id = itens_ci[0].get("contractItemId")

        # Estratégia 3: Histórico recente (apenas último recurso)
        if not contract_item_id:
            r_oc = requests.get(f"{API_URL}/contract-item-request/search?LicensePlate={placa_api}", headers=headers)
            if r_oc.status_code == 200:
                ocs = r_oc.json().get("data", [])
                for oc in ocs:
                    if oc.get("contractItemId"):
                        contract_item_id = oc.get("contractItemId")
                        break

        if not contract_item_id:
            return jsonify({"isSuccess": False, "erro": f"Carro {placa_api} não possui contractItemId ativo na API."}), 400
        
        # 4. Bloco de Manutenção limpo com a chave oficial
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

        # Anexa PDF do checklist se existir fisicamente no servidor
        files_payload = []
        caminho_pdf = checklist_alvo.get("caminho_pdf")
        if caminho_pdf and os.path.exists(caminho_pdf):
            try:
                with open(caminho_pdf, "rb") as f_pdf:
                    b64_pdf = base64.b64encode(f_pdf.read()).decode()
                files_payload.append({
                    "fileName": checklist_alvo.get("pdf_nome") or f"Checklist_{placa_api}.pdf",
                    "base64Contents": b64_pdf,
                    "fileType": "UnidentifiedDocument"
                })
            except Exception as e_pdf:
                print(f"Aviso: não foi possível codificar PDF do checklist: {e_pdf}")

        # Monta a raiz do Payload primeiro
        payload_bluefleet = {
            "contractItemRequestReasonId": motivo_id,
            "contractItemRequestReasonClassId": classe_id,
            "maintenance": bloco_manutencao,
            "files": files_payload
        }

        # Adiciona variáveis extras NA RAIZ do JSON (e nunca dentro da manutenção)
        if tipo_veiculo == "alugado":
            payload_bluefleet["updateContractItemDriver"] = False
            payload_bluefleet["hasPickUpReturnService"] = edicoes.get("leva_e_traz", checklist_alvo.get("leva_e_traz", False))
            
        if is_revisao:
            payload_bluefleet["hasDefaultMaintenancePlan"] = True
            payload_bluefleet["maintenancePlanRevisionId"] = "149"
        
        # 5. Envio da Ocorrência para a Blue Fleet
        url_criacao = f"{API_URL}/contract-item/{contract_item_id}/contract-item-request"
        resp_bf = requests.post(url_criacao, headers=headers, json=payload_bluefleet)
        
        if resp_bf.status_code in [200, 201]:
            resp_dados = resp_bf.json()
            req_id = resp_dados.get("data", {}).get("contractItemRequestId")
            
            # 6. Avanço Automático do Workflow até Aguardando Orçamento
            try:
                # Passo 1: Pré-Agendamento -> Confirmação de Agenda
                requests.post(f"{API_URL}/contract-item-request/{req_id}/pre-scheduling", 
                              headers=headers, json={"providerId": 197, "scheduledFor": data_iso}, timeout=5)
                # Passo 2: Confirmação de Agenda -> Aguardando Chegada
                requests.post(f"{API_URL}/contract-item-request/{req_id}/schedule-confirmation", 
                              headers=headers, timeout=5)
                # Passo 3: Aguardando Chegada -> Aguardando Orçamento (Data de Entrada)
                r_chk = requests.post(f"{API_URL}/contract-item-request/{req_id}/waiting-checkin", 
                                      headers=headers, json={"confirmVehicleCheckinDate": data_iso}, timeout=5)
                print(f"Avanço de fila concluído para ocorrência {req_id} (Status {r_chk.status_code})")
            except Exception as e_chk:
                print(f"Aviso: falha na transição automática de fila: {e_chk}")

            with open(caminho_pendentes, 'w', encoding='utf-8') as f:
                json.dump(nova_fila, f, ensure_ascii=False, indent=4)
                
            return jsonify({
                "isSuccess": True, 
                "mensagem": f"Ocorrência criada com sucesso para a placa {placa_api}!",
                "contractItemRequestId": req_id
            }), 200
        else:
            return jsonify({"isSuccess": False, "erro": f"Blue Fleet recusou: {resp_bf.text}"}), 400
            
    except Exception as e:
        return jsonify({"isSuccess": False, "erro": f"Erro interno ao disparar: {str(e)}"}), 500

@app.route("/painel/descartar/<id_aprovacao>", methods=["POST"])
@app.route("/painel/rejeitar/<id_aprovacao>", methods=["POST"])
@app.route("/painel-nevada/rejeitar/<id_aprovacao>", methods=["POST"])
def painel_rejeitar(id_aprovacao):
    caminho_pendentes = os.path.join(os.path.dirname(__file__), 'checklists_pendentes.json')
    if not os.path.exists(caminho_pendentes):
        return jsonify({"isSuccess": False, "erro": "Nenhum checklist pendente."}), 404
        
    try:
        with open(caminho_pendentes, 'r', encoding='utf-8') as f:
            pendentes = json.load(f)
    except Exception:
        return jsonify({"isSuccess": False, "erro": "Erro ao ler arquivo."}), 500
        
    nova_fila = [item for item in pendentes if item.get("id_aprovacao") != id_aprovacao]
    
    with open(caminho_pendentes, 'w', encoding='utf-8') as f:
        json.dump(nova_fila, f, ensure_ascii=False, indent=4)
        
    return jsonify({"isSuccess": True, "mensagem": "Checklist descartado com sucesso."}), 200

@app.route("/api/veiculos/sugestoes", methods=["GET"])
def api_sugestoes():
    busca = request.args.get("q", "").strip().upper().replace("-", "")
    if len(busca) < 3:
        return jsonify({"placas": []})
        
    try:
        caminho_cache = os.path.join(os.path.dirname(__file__), 'placas_cache.json')
        
        if os.path.exists(caminho_cache):
            with open(caminho_cache, 'r', encoding='utf-8') as f:
                cache_frota = json.load(f)
        else:
            cache_frota = []
            
        placas_filtradas = [p for p in cache_frota if p.replace("-", "").startswith(busca)]
        placas_filtradas.sort()
        
        return jsonify({"placas": placas_filtradas[:50]})
        
    except Exception as e:
        print(f"Erro ao ler cache de sugestões: {e}")
        return jsonify({"placas": []})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

    app.run(host="0.0.0.0", port=5000)