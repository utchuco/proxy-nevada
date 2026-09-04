import os
import base64
import requests
from flask import Flask, render_template, request
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
        ocorrencia_atual = None
        arquivos_ocorrencia = None # Variável para pescar os arquivos/checklists

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
                                ocorrencia_atual = oc
                                
                                # AQUI ENTRA A MÁGICA DOS ARQUIVOS
                                req_id = oc.get("contractItemRequestId")
                                if req_id:
                                    try:
                                        r_files = requests.get(f"{API_URL}/contract-item-request/{req_id}/files", headers=headers)
                                        if r_files.status_code == 200:
                                            arquivos_ocorrencia = r_files.json()
                                    except Exception:
                                        pass
                                
                                r_titular = requests.get(f"{API_URL}/vehicle?LicensePlate={placa_titular}", headers=headers)
                                if r_titular.status_code == 200:
                                    lista_titular = r_titular.json().get("data", [])
                                    if lista_titular:
                                        veiculo_titular = lista_titular[0]
                                        break
            except Exception:
                pass

        return render_template("resultado.html", veiculo=veiculo, veiculo_titular=veiculo_titular, arquivos_ocorrencia=arquivos_ocorrencia)

    except requests.exceptions.HTTPError as err_http:
        return render_template("index.html", erro=f"Falha na comunicação: {err_http}")
    except Exception as e:
        return render_template("index.html", erro=f"Erro interno do sistema: {str(e)}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)