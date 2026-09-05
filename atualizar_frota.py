import os
import json
import base64
import requests
import time
from dotenv import load_dotenv

DIRETORIO_BASE = "/home/ubuntu/proxy-nevada"
load_dotenv(os.path.join(DIRETORIO_BASE, ".env"))

CLIENT_ID = os.getenv("BLUEFLEET_CLIENT_ID")
CLIENT_SECRET = os.getenv("BLUEFLEET_CLIENT_SECRET")
AUTH_URL = "https://auth.bluefleet.com.br/connect/token"
API_URL = "https://api.bluefleet.com.br"
ARQUIVO_CACHE = os.path.join(DIRETORIO_BASE, "placas_cache.json")

def atualizar_cache():
    print("Iniciando extração profunda da frota com Anti-Bloqueio...")
    todas_placas = set()
    
    try:
        credentials = f"{CLIENT_ID}:{CLIENT_SECRET}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        headers_auth = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        res_auth = requests.post(AUTH_URL, headers=headers_auth, data={"grant_type": "client_credentials"})
        res_auth.raise_for_status()
        token = res_auth.json().get("access_token")

        headers_api = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        parametro_offset = 0 
        
        while True:
            # Count explícito para garantir a paginação exata
            url_busca = f"{API_URL}/vehicle?Offset={parametro_offset}&Count=100"
            res_api = requests.get(url_busca, headers=headers_api)
            
            # Se o firewall bloquear, respira e tenta a mesma página novamente
            if res_api.status_code == 429:
                print("Limite da API atingido. Pausando por 5 segundos...")
                time.sleep(5)
                continue
                
            res_api.raise_for_status()
            dados = res_api.json().get("data", [])
            
            if not dados:
                break
                
            quantidade_antes = len(todas_placas)
            
            for v in dados:
                placa = v.get("licensePlate")
                if placa:
                    todas_placas.add(placa)
            
            if len(todas_placas) == quantidade_antes:
                break
                
            parametro_offset += 1
            print(f"Página {parametro_offset} processada. Total acumulado: {len(todas_placas)} placas.")
            
            # Pausa para ser gentil com o servidor da Blue Fleet
            time.sleep(1.5)

    except Exception as e:
        print(f"ERRO de rede ou API: {e}")
        
    finally:
        # Garante que o arquivo será sobrescrito se tivermos capturado dados
        if todas_placas:
            with open(ARQUIVO_CACHE, 'w', encoding='utf-8') as f:
                json.dump(list(todas_placas), f)
            print(f"Arquivo salvo com sucesso! {len(todas_placas)} placas no cofre local.")

if __name__ == "__main__":
    atualizar_cache()