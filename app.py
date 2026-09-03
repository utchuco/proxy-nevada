import os
import base64
import requests
from flask import Flask, render_template, request
from dotenv import load_dotenv

# Carrega as variáveis do arquivo .env
load_dotenv()

app = Flask(__name__)

# Configurações da Blue Fleet (Produção)
CLIENT_ID = os.getenv("BLUEFLEET_CLIENT_ID")
CLIENT_SECRET = os.getenv("BLUEFLEET_CLIENT_SECRET")
AUTH_URL = "https://auth.bluefleet.com.br/connect/token"
API_URL = "https://api.bluefleet.com.br"

def get_access_token():
    """Gera o token OAuth2.0 codificando Client ID e Secret em Base64"""
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
    response.raise_for_status()  # Lança erro se a senha estiver errada
    
    return response.json().get("access_token")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/buscar", methods=["POST"])
def buscar():
    placa = request.form.get("placa", "").strip().upper()
    
    if not placa:
        return render_template("index.html", erro="Por favor, digite uma placa válida.")

    try:
        # 1. Obtém a permissão
        token = get_access_token()
        
        # 2. Prepara a busca
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        
        # Faz a requisição de busca pela placa
        # Obs: Ajuste o final da URL (/veiculos?placa=) se o endpoint exato for outro
        url_busca = f"{API_URL}/veiculos?placa={placa}"
        response = requests.get(url_busca, headers=headers)
        response.raise_for_status()
        
        dados = response.json()
        
        # 3. Trava de segurança: verifica se a resposta veio vazia (Impede o "Erro 0")
        if not dados or (isinstance(dados, list) and len(dados) == 0):
            return render_template("index.html", erro=f"Nenhum veículo encontrado para a placa {placa}.")
        
        # 4. Extrai o veículo com segurança (pega o primeiro se for lista)
        veiculo = dados[0] if isinstance(dados, list) else dados

        return render_template("resultado.html", veiculo=veiculo)

    except requests.exceptions.HTTPError as err_http:
        # Erros de API (ex: 401 Não Autorizado, 404 Não Encontrado)
        return render_template("index.html", erro=f"Falha na API: {err_http}")
    except Exception as e:
        # Qualquer outro erro interno
        return render_template("index.html", erro=f"Erro interno do sistema: {str(e)}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)