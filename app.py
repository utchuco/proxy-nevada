import os
import requests
import base64
from flask import Flask, render_template, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

CLIENT_ID = os.getenv('BLUEFLEET_CLIENT_ID')
CLIENT_SECRET = os.getenv('BLUEFLEET_CLIENT_SECRET')

# URL base mapeada pelo arquivo do Swagger
API_BASE_URL = "https://api.bluefleet.com.br"

def gerar_token_bluefleet():
    """
    Gera o Token usando Basic Auth com Base64 conforme documentação da Blue Fleet.
    """
    url_auth = "https://auth.bluefleet.com.br/connect/token"
    
    # Junta ID e Secret e converte para Base64
    credenciais = f"{CLIENT_ID}:{CLIENT_SECRET}".encode("utf-8")
    credenciais_b64 = base64.b64encode(credenciais).decode("utf-8")
    
    headers = {
        "Authorization": f"Basic {credenciais_b64}",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    payload = "grant_type=client_credentials"
    
    try:
        response = requests.post(url_auth, headers=headers, data=payload)
        response.raise_for_status()
        return response.json().get("access_token")
    except Exception as e:
        print(f"Erro ao gerar token: {e}")
        return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/buscar', methods=['POST'])
def buscar():
    placa = request.form.get('placa').upper().strip()
    
    # 1. Gera a chave de acesso
    token = gerar_token_bluefleet()
    
    # Se ainda não temos o link exato de autenticação deles, carregamos dados de teste visual
    if not token:
        return render_template('resultado.html', 
                               placa=placa, 
                               cliente="(Aguardando configuração do Token)", 
                               checklists=[{"numero": "Teste-01", "data": "2026-09-03", "status": "Simulação de Tela"}])

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    try:
        # 2. Busca o ID do Cliente atrelado ao veículo
        # Rota definida no Swagger da Blue Fleet
        resp_veiculo = requests.get(f"{API_BASE_URL}/vehicle?LicensePlate={placa}", headers=headers)
        dados_veiculo = resp_veiculo.json()
        
        # Simulando a extração do ID (depende da estrutura exata da resposta)
        cliente_id = dados_veiculo[0].get("customerId") if dados_veiculo else None
        
        cliente_nome = "Não identificado"
        if cliente_id:
            # Busca o nome real do cliente na rota /commercial/customer/{customerId}
            resp_cliente = requests.get(f"{API_BASE_URL}/commercial/customer/{cliente_id}", headers=headers)
            cliente_nome = resp_cliente.json().get("name", "Não identificado")
            
        # 3. Busca o histórico de checklists e chamados daquela placa
        resp_checklists = requests.get(f"{API_BASE_URL}/contract-item-request/search?LicensePlate={placa}", headers=headers)
        dados_checklists = resp_checklists.json()
        
        # Mapeando os resultados para enviar para a tela
        checklists_formatados = []
        for item in dados_checklists.get("items", [])[:5]: # Pegando apenas os 5 últimos
            checklists_formatados.append({
                "numero": item.get("contractItemRequestNumber", "Sem número"),
                "data": item.get("initialDate", "Sem data")[:10], # Pega só a parte da data (YYYY-MM-DD)
                "status": "Registrado"
            })
            
        return render_template('resultado.html', placa=placa, cliente=cliente_nome, checklists=checklists_formatados)
        
    except Exception as e:
        return f"Erro ao comunicar com a API: {str(e)}"

if __name__ == '__main__':
    app.run(debug=True)