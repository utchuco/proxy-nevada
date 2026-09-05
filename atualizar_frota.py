import os
import json
import base64
import requests
from dotenv import load_dotenv

# Caminho absoluto da pasta do servidor (importante para o Cron achar o arquivo)
DIRETORIO_BASE = "/home/ubuntu/proxy-nevada"

# Carrega as senhas do .env
load_dotenv(os.path.join(DIRETORIO_BASE, ".env"))

CLIENT_ID = os.getenv("BLUEFLEET_CLIENT_ID")
CLIENT_SECRET = os.getenv("BLUEFLEET_CLIENT_SECRET")
AUTH_URL = "https://auth.bluefleet.com.br/connect/token"
API_URL = "https://api.bluefleet.com.br"
ARQUIVO_CACHE = os.path.join(DIRETORIO_BASE, "placas_cache.json")

def atualizar_cache():
    print("Iniciando atualização do cache de frota...")
    try:
        # 1. Pegar o Token
        credentials = f"{CLIENT_ID}:{CLIENT_SECRET}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        headers_auth = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        res_auth = requests.post(AUTH_URL, headers=headers_auth, data={"grant_type": "client_credentials"})
        res_auth.raise_for_status()
        token = res_auth.json().get("access_token")

        # 2. Baixar a Frota
        headers_api = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        url_busca = f"{API_URL}/vehicle?Count=5000"
        res_api = requests.get(url_busca, headers=headers_api)
        res_api.raise_for_status()
        
        # 3. Extrair as Placas e Remover Duplicatas
        dados = res_api.json().get("data", [])
        placas = list(set([v.get("licensePlate") for v in dados if v.get("licensePlate")]))
        
        # 4. Salvar no arquivo JSON local
        with open(ARQUIVO_CACHE, 'w', encoding='utf-8') as f:
            json.dump(placas, f)
            
        print(f"Sucesso! {len(placas)} placas salvas no cofre local.")

    except Exception as e:
        print(f"ERRO ao atualizar cache: {e}")

if __name__ == "__main__":
    atualizar_cache()