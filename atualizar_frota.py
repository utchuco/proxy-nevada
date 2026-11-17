import os
import json
import base64
import requests
import time
from dotenv import load_dotenv

DIRETORIO_BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(DIRETORIO_BASE, ".env"))

CLIENT_ID = os.getenv("BLUEFLEET_CLIENT_ID")
CLIENT_SECRET = os.getenv("BLUEFLEET_CLIENT_SECRET")
AUTH_URL = "https://auth.bluefleet.com.br/connect/token"
API_URL = "https://api.bluefleet.com.br"
ARQUIVO_CACHE = os.path.join(DIRETORIO_BASE, "placas_cache.json")

def atualizar_cache():
    print("Iniciando extração da frota com tolerância a falhas...")
    todas_placas = set()
    
    try:
        credentials = f"{CLIENT_ID}:{CLIENT_SECRET}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        headers_auth = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json"
        }
        
        token = None
        for tentativa in range(3):
            try:
                res_auth = requests.post(AUTH_URL, headers=headers_auth, data={"grant_type": "client_credentials"}, timeout=10)
                if res_auth.status_code == 200:
                    token = res_auth.json().get("access_token")
                    break
            except Exception as e:
                print(f"Tentativa {tentativa+1} com Header Basic falhou: {e}")

            time.sleep(0.5)

            try:
                headers_fallback = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json"
                }
                data_fallback = {
                    "grant_type": "client_credentials",
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET
                }
                res_auth = requests.post(AUTH_URL, headers=headers_fallback, data=data_fallback, timeout=10)
                if res_auth.status_code == 200:
                    token = res_auth.json().get("access_token")
                    break
            except Exception as e:
                print(f"Tentativa {tentativa+1} com Body falhou: {e}")

            time.sleep(1)

        if not token:
            print("ERRO de rede ou API: Não foi possível obter token de acesso da Blue Fleet.")
            return

        headers_api = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        
        offset = 0 
        count = 100
        tentativas_falhas = 0
        
        while True:
            url_busca = f"{API_URL}/vehicle?Offset={offset}&Count={count}"
            res_api = requests.get(url_busca, headers=headers_api)
            
            if res_api.status_code == 429:
                tentativas_falhas += 1
                tempo_espera = 5 * tentativas_falhas
                print(f"Bloqueio da API (429) no offset {offset}. Pausando por {tempo_espera}s...")
                
                if tentativas_falhas > 5:
                    print("Limite máximo atingido. Interrompendo a extração.")
                    break
                    
                time.sleep(tempo_espera)
                continue 
                
            res_api.raise_for_status()
            tentativas_falhas = 0
            
            dados = res_api.json().get("data", [])
            
            if not dados:
                break 
                
            for v in dados:
                placa = v.get("licensePlate")
                if placa:
                    todas_placas.add(placa)
            
            print(f"Progresso: {len(todas_placas)} placas coletadas...")
            offset += len(dados)
            time.sleep(2)

    except Exception as e:
        print(f"ERRO de rede ou API: {e}")
        
    finally:
        if todas_placas:
            with open(ARQUIVO_CACHE, 'w', encoding='utf-8') as f:
                json.dump(list(todas_placas), f)
            print(f"Arquivo salvo com sucesso! {len(todas_placas)} placas no cofre local.")
        else:
            print("Nenhuma placa coletada. O arquivo não foi alterado.")

if __name__ == "__main__":
    atualizar_cache()