import os
import base64
import requests
from flask import Flask, render_template, request, send_file
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
        espiao_api = None # O nosso detetive

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
                                
                                # --- INÍCIO DA CAPTURA DO ESPIÃO ---
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
                                # --- FIM DA CAPTURA ---

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
    
    # A senha da frota
    if senha != '1234':
        return "Acesso negado: Senha incorreta.", 403
    
    # Limpa a placa para buscar
    placa_limpa = placa.replace('-', '').replace(' ', '').upper()
    
    # Ordem de busca: 2026 primeiro, depois 2025
    pastas_busca = ['documentos/2026', 'documentos/2025']
    
    for pasta in pastas_busca:
        if os.path.exists(pasta):
            # O os.walk vasculha a pasta principal e todas as subpastas lá dentro!
            for raiz, subpastas, arquivos in os.walk(pasta):
                for arquivo in arquivos:
                    nome_arquivo_limpo = arquivo.replace('-', '').replace(' ', '').upper()
                    
                    if placa_limpa in nome_arquivo_limpo and arquivo.upper().endswith('.PDF'):
                        # Usa a 'raiz' atual onde o arquivo foi achado
                        caminho_completo = os.path.join(raiz, arquivo)
                        return send_file(caminho_completo, mimetype='application/pdf')
    
    return f"Documento não encontrado para a placa {placa}.", 404

@app.route('/upload-crlv', methods=['GET', 'POST'])
def upload_crlv():
    if request.method == 'POST':
        senha = request.form.get('senha')
        
        # Senha super secreta para ninguém acessar essa tela além de você
        if senha != 'admin123':
            return "Acesso negado.", 403
            
        ano = request.form.get('ano')
        arquivos = request.files.getlist('arquivos_pdf')
        
        # Cria a pasta do ano automaticamente se ela não existir
        pasta_destino = f"documentos/{ano}"
        os.makedirs(pasta_destino, exist_ok=True)
        
        contador = 0
        for arquivo in arquivos:
            if arquivo.filename.upper().endswith('.PDF'):
                # Salva o arquivo na pasta correta na Oracle
                caminho_salvar = os.path.join(pasta_destino, arquivo.filename)
                arquivo.save(caminho_salvar)
                contador += 1
                
        return f"<h2 style='color: green;'>Sucesso! {contador} arquivos salvos na pasta {ano}.</h2> <a href='/upload-crlv'>Voltar</a>"

    # Se for GET, mostra a telinha de Upload direto no navegador
    return '''
    <div style="text-align: center; font-family: Arial; padding: 50px;">
        <h2>📤 Painel de Upload - CRLV</h2>
        <form method="POST" enctype="multipart/form-data" style="display: inline-block; text-align: left; background: #f4f4f4; padding: 20px; border-radius: 10px;">
            <p><b>Senha de Admin:</b><br> <input type="password" name="senha" required style="padding: 8px; width: 100%;"></p>
            <p><b>Ano do Documento:</b><br> 
                <select name="ano" style="padding: 8px; width: 100%;">
                    <option value="2026">2026</option>
                    <option value="2025">2025</option>
                </select>
            </p>
            <p><b>Selecione os PDFs:</b><br> <input type="file" name="arquivos_pdf" multiple accept=".pdf" style="padding: 8px;"></p>
            <button type="submit" style="padding: 10px 20px; background: #0056b3; color: white; border: none; border-radius: 5px; cursor: pointer;">Fazer Upload</button>
        </form>
    </div>
    '''

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)