import os
import json
import base64
import requests
from flask import Flask, render_template, request, send_file, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

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
        
        veiculo = lista_veiculos[0]
        veiculo_titular = None
        arquivos_ocorrencia = None 
        espiao_api = None 

        if veiculo.get("vehicleStatusId") == 14:
            try:
                r_ocorrencia = requests.get(f"{API_URL}/contract-item-request/search?LicensePlate={placa}", headers=headers)
                if r_ocorrencia.status_code == 200:
                    ocorrencias = r_ocorrencia.json().get("data", [])
                    if ocorrencias:
                        ocorrencias.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
                        for oc in ocorrencias:
                            placa_titular = oc.get("licensePlate")
                            if placa_titular and placa_titular != placa:
                                req_id = oc.get("contractItemRequestId")
                                
                                if req_id:
                                    url_arquivos = f"{API_URL}/contract-item-request/{req_id}/files"
                                    try:
                                        r_files = requests.get(url_arquivos, headers=headers)
                                        espiao_api = {
                                            "status_http": r_files.status_code,
                                            "url_testada": url_arquivos,
                                            "resposta_bruta": r_files.text
                                        }
                                        if r_files.status_code == 200:
                                            arquivos_ocorrencia = r_files.json()
                                    except Exception as e:
                                        espiao_api = {"erro_interno_python": str(e)}

                                r_titular = requests.get(f"{API_URL}/vehicle?LicensePlate={placa_titular}", headers=headers)
                                if r_titular.status_code == 200:
                                    lista_titular = r_titular.json().get("data", [])
                                    if lista_titular:
                                        veiculo_titular = lista_titular[0]
                                        break
            except Exception:
                pass

        return render_template("resultado.html", veiculo=veiculo, veiculo_titular=veiculo_titular, arquivos_ocorrencia=arquivos_ocorrencia, espiao_api=espiao_api)

    except requests.exceptions.HTTPError as err_http:
        return render_template("index.html", erro=f"Falha na comunicação: {err_http}")
    except Exception as e:
        return render_template("index.html", erro=f"Erro interno do sistema: {str(e)}")


@app.route('/crlv', methods=['POST'])
def acessar_crlv():
    placa = request.form.get('placa')
    senha = request.form.get('senha')
    
    if senha != '1234':
        return "Acesso negado: Senha incorreta.", 403
    
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
            
        placas_filtradas = [placa for placa in cache_frota if placa.replace("-", "").startswith(busca)]
        placas_filtradas.sort()
        
        return jsonify({"placas": placas_filtradas[:50]})
        
    except Exception as e:
        print(f"Erro ao ler cache de sugestões: {e}")
        return jsonify({"placas": []})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)