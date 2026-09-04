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
        
        # 1ª Busca: Ficha técnica do veículo
        url_busca = f"{API_URL}/vehicle?LicensePlate={placa}"
        response = requests.get(url_busca, headers=headers)
        response.raise_for_status()
        
        lista_veiculos = response.json().get("data", [])
        
        if not lista_veiculos:
            return render_template("index.html", erro=f"Veículo não encontrado para a placa {placa}.")
        
        veiculo = lista_veiculos[0]
        
        # Variáveis para guardar o resultado da nossa rede de pesca
        dados_reserva = None
        dados_ocorrencia = None

        # Se for Status 14 (LOCADO VEÍCULO RESERVA), dispara a busca dupla
        if veiculo.get("vehicleStatusId") == 14:
            
            # Tentativa A: Rota de Reservas
            try:
                r_reserva = requests.get(f"{API_URL}/vehicle/reservation?licensePlate={placa}", headers=headers)
                if r_reserva.status_code == 200:
                    dados_reserva = r_reserva.json()
            except Exception:
                pass
                
            # Tentativa B: Rota de Ocorrências
            try:
                r_ocorrencia = requests.get(f"{API_URL}/contract-item-request/search?LicensePlate={placa}", headers=headers)
                if r_ocorrencia.status_code == 200:
                    dados_ocorrencia = r_ocorrencia.json()
            except Exception:
                pass

        return render_template("resultado.html", veiculo=veiculo, dados_reserva=dados_reserva, dados_ocorrencia=dados_ocorrencia)

    except requests.exceptions.HTTPError as err_http:
        return render_template("index.html", erro=f"Falha na comunicação: {err_http}")
    except Exception as e:
        return render_template("index.html", erro=f"Erro interno do sistema: {str(e)}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)