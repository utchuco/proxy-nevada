import os
import json
import base64
import requests
import io 
import re 
from flask import Flask, render_template, request, send_file, jsonify
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
        
        url_busca = f"{API_URL}/vehicle?LicensePlate={placa}"
        response = requests.get(url_busca, headers=headers)
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

        # 1. Define quem é quem com base na pesquisa inicial
        if str(veiculo_base.get("vehicleStatusId")) == "14":
            carro_reserva = veiculo_base
        else:
            carro_titular = veiculo_base

        # 2. OMNI-RADAR: Dispara contra todas as rotas catalogadas no Swagger
        endpoints_radar = [
            f"{API_URL}/contract-item-request/search?LicensePlate={placa}",
            f"{API_URL}/contract-item/search?LicensePlate={placa}",
            f"{API_URL}/vehicle/{veiculo_id}/contract-item",
            f"{API_URL}/vehicle/reservation?licensePlate={placa}"
        ]

        placas_encontradas = set()
        
        for url in endpoints_radar:
            try:
                r_teste = requests.get(url, headers=headers, timeout=4)
                if r_teste.status_code == 200:
                    texto_api = r_teste.text.upper()
                    
                    # Detecta pelo texto se existe alguma menção a "Aguardando Devolução" ou "Reserva"
                    if "RESERVA" in texto_api and "AGUARDANDO DEVOLU" in texto_api:
                        reserva_oculta_detectada = True

                    # Pente fino: caça qualquer padrão de placa no meio do JSON (Mercosul e Antiga)
                    placas_regex = set(re.findall(r'([A-Z]{3}[ -]?[0-9][A-Z0-9][0-9]{2})', texto_api))
                    
                    for p in placas_regex:
                        p_clean = p.replace("-", "").replace(" ", "")
                        if p_clean != placa_limpa_base:
                            placas_encontradas.add(p_clean)
            except Exception:
                continue

        # 3. Valida as placas suspeitas na rota principal de veículos
        for p_clean in placas_encontradas:
            if carro_titular and carro_reserva:
                break # Já achou os dois, para a busca
                
            try:
                r_valida = requests.get(f"{API_URL}/vehicle?LicensePlate={p_clean}", headers=headers, timeout=3)
                if r_valida.status_code == 200 and r_valida.json().get("data"):
                    v_teste = r_valida.json().get("data")[0]
                    status_id = str(v_teste.get("vehicleStatusId"))
                    
                    # Conecta os pontos garantindo que as variáveis certas sejam preenchidas
                    if carro_titular and not carro_reserva and status_id == "14":
                        carro_reserva = v_teste
                        reserva_oculta_detectada = False
                    elif carro_reserva and not carro_titular and status_id != "14":
                        carro_titular = v_teste
            except Exception:
                continue

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
                        nome_bruto = str(arquivo.get("filename", arquivo.get("fileName", arquivo.get("name", ""))))
                        nome_limpo = nome_bruto.upper().replace("-", "").replace(" ", "")
                        
                        if placa_limpa in nome_limpo and placa_limpa != "":
                            id_arquivo = arquivo.get("id", arquivo.get("fileId"))
                            url_direta = arquivo.get("url")
                            
                            if url_direta:
                                r_pdf = requests.get(url_direta)
                            else:
                                url_api = f"{API_URL}/contract-item-request/{req_id}/files/{id_arquivo}"
                                r_pdf = requests.get(url_api, headers=headers)
                            
                            if r_pdf.status_code == 200:
                                return send_file(
                                    io.BytesIO(r_pdf.content),
                                    mimetype='application/pdf',
                                    as_attachment=False, 
                                    download_name=f"Checklist_{placa_limpa}.pdf"
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