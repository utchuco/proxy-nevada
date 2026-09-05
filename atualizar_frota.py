import os
import json
import base64
import requests
from dotenv import load_dotenv

DIRETORIO_BASE = "/home/ubuntu/proxy-nevada"
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

        # 2. Baixar a Frota com Paginação (Loop)
        headers_api = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        todas_placas = set()
        offset = 0
        
        while True:
            # O parâmetro Offset permite pular os registros já lidos
            url_busca = f"{API_URL}/vehicle?Offset={offset}"
            res_api = requests.get(url_busca, headers=headers_api)
            res_api.raise_for_status()
            
            dados = res_api.json().get("data", [])
            
            # Se a Blue Fleet retornar uma lista vazia, significa que já baixamos tudo
            if not dados:
                break
                
            for v in dados:
                placa = v.get("licensePlate")
                if placa:
                    todas_placas.add(placa)
            
            # Aumenta o Offset pela quantidade de itens recebidos para pedir a próxima "página"
            offset += len(dados)
            print(f"Progresso: {len(todas_placas)} placas coletadas...")

        # 3. Salvar no arquivo JSON local
        with open(ARQUIVO_CACHE, 'w', encoding='utf-8') as f:
            json.dump(list(todas_placas), f)
            
        print(f"Sucesso Total! {len(todas_placas)} placas salvas no cofre local.")

    except Exception as e:
        print(f"ERRO ao atualizar cache: {e}")

if __name__ == "__main__":
    atualizar_cache()