import os
import json
import base64
import requests
import re 
from datetime import datetime, timezone
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
    
    if not dados or not dados.get("placa"):
        return jsonify({"isSuccess": False, "erro": "Dados ou placa ausentes."}), 400

    agora_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    novo_pendente = {
        "id_aprovacao": str(uuid.uuid4()),
        "placa": dados.get("placa").upper().replace("-", "").strip(),
        "km_atual": dados.get("km"),
        "tipo_veiculo": dados.get("tipo_veiculo", "alugado").lower(), # "disponivel" ou "alugado"
        "motivo_id": str(dados.get("motivo_id", "1")),
        "motivo_classe": str(dados.get("motivo_classe", "1")),
        "plano_revisao_id": str(dados.get("plano_revisao_id", "149")),
        "descricao": dados.get("descricao", "Revisão preventiva"),
        "data_agendamento": dados.get("data_agendamento", agora_iso),
        "solicitante": dados.get("solicitante", "Equipe Nevada"),
        "telefone": dados.get("telefone", "11999999999"),
        "email": dados.get("email", ""),
        "leva_e_traz": bool(dados.get("leva_e_traz", False)),
        "data_recebimento": agora_iso,
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
            
    return render_template("painel.html", checklists=pendentes)

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
        
    placa = checklist_alvo.get("placa", "").strip().upper()
    tipo_veiculo = checklist_alvo.get("tipo_veiculo", "alugado")
    
    # Formata a placa com hífen para a consulta na Blue Fleet (ex: UGK-7I93)
    if len(placa) == 7 and "-" not in placa:
        placa_api = f"{placa[:3]}-{placa[3:]}"
    else:
        placa_api = placa
    
    try:
        token = get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        # 1. Resolve o ContractItemId de forma infalível
        contract_item_id = None
        
        # Estratégia 1: Procura pelo histórico de ocorrências da placa
        r_oc = requests.get(f"{API_URL}/contract-item-request/search?LicensePlate={placa_api}", headers=headers)
        if r_oc.status_code == 200:
            ocs = r_oc.json().get("data", [])
            for oc in ocs:
                if oc.get("contractItemId"):
                    contract_item_id = oc.get("contractItemId")
                    break
        
        # Estratégia 2: Se não houver histórico, busca via contrato do carro
        if not contract_item_id:
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

        if not contract_item_id:
            return jsonify({"isSuccess": False, "erro": f"Carro {placa_api} não possui contractItemId ativo na API."}), 400
        
        # 2. Constrói o Payload dinamicamente com base no tipo de manutenção e veículo
        is_revisao = str(checklist_alvo.get("motivo_id")) in ["1", "PreventiveMaintenance"]
        # Data no formato ISO UTC exato de ontem
        agora_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        data_agendamento = checklist_alvo.get("data_agendamento") or agora_iso
        
        email_padrao = checklist_alvo.get("email") or "frota@locadoranevada.com.br"
        nome_solicitante = checklist_alvo.get("solicitante") or "Equipe Nevada"
        telefone_solicitante = checklist_alvo.get("telefone") or "11999999999"

        bloco_manutencao = {
            "currentOdometer": int(checklist_alvo.get("km_atual", 0)),
            "description": checklist_alvo.get("descricao", "Manutenção"),
            "providerId": 197,  # Oficina Nevada
            "city": "Guarulhos",
            "state": "SP",
            "country": "BR",
            "latitude": -23.4656,
            "longitude": -46.5330,
            "requesterName": nome_solicitante,
            "requesterPhone": telefone_solicitante,
            "requesterEmail": email_padrao,
            "scheduledFor": data_agendamento,
            "scheduling1": data_agendamento
        }

        # Apenas inclui os campos opcionais de motorista se for alugado
        if tipo_veiculo == "alugado":
            bloco_manutencao["updateContractItemDriver"] = False
            bloco_manutencao["hasPickUpReturnService"] = checklist_alvo.get("leva_e_traz", False)
            
        payload_bluefleet = {
            "contractItemRequestReasonId": checklist_alvo.get("motivo_id"),
            "contractItemRequestReasonClassId": checklist_alvo.get("motivo_classe"),
            "maintenance": bloco_manutencao,
            "files": []
        }
        
        if is_revisao:
            payload_bluefleet["hasDefaultMaintenancePlan"] = True
            payload_bluefleet["maintenancePlanRevisionId"] = checklist_alvo.get("plano_revisao_id", "149")
        
        # 3. Dispara a criação para a Blue Fleet
        url_criacao = f"{API_URL}/contract-item/{contract_item_id}/contract-item-request"
        resp_bf = requests.post(url_criacao, headers=headers, json=payload_bluefleet)
        
        if resp_bf.status_code == 200:
            with open(caminho_pendentes, 'w', encoding='utf-8') as f:
                json.dump(nova_fila, f, ensure_ascii=False, indent=4)
            return jsonify({"isSuccess": True, "mensagem": f"Ocorrência criada com sucesso para a placa {placa_api}!"}), 200
        else:
            return jsonify({"isSuccess": False, "erro": f"Blue Fleet recusou: {resp_bf.text}"}), 400
            
    except Exception as e:
        return jsonify({"isSuccess": False, "erro": f"Erro interno ao disparar: {str(e)}"}), 500

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