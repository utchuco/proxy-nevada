import os
import re
import json
import time
import base64
import requests

CLIENT_ID = os.getenv("BLUEFLEET_CLIENT_ID")
CLIENT_SECRET = os.getenv("BLUEFLEET_CLIENT_SECRET")
AUTH_URL = "https://auth.bluefleet.com.br/connect/token"
API_URL = "https://api.bluefleet.com.br"

def get_access_token():
    client_id = os.getenv("BLUEFLEET_CLIENT_ID")
    client_secret = os.getenv("BLUEFLEET_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError("Credenciais ausentes no servidor (verifique o arquivo .env)")
    credentials = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(credentials.encode()).decode()
    headers = {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {"grant_type": "client_credentials"}
    response = requests.post(AUTH_URL, headers=headers, data=data, timeout=10)
    response.raise_for_status()
    return response.json().get("access_token")

def get_blindado(url, headers, rapido=False):
    tentativas = 1 if rapido else 3
    tempo_limite = 3 if rapido else 5
    
    for _ in range(tentativas):
        try:
            resp = requests.get(url, headers=headers, timeout=tempo_limite)
            if resp.status_code == 200:
                return resp
            elif resp.status_code == 429:
                time.sleep(1)
        except Exception:
            pass
        if not rapido:
            time.sleep(0.5)
    
    try:
        return requests.get(url, headers=headers, timeout=tempo_limite)
    except Exception:
        resp_erro = requests.Response()
        resp_erro.status_code = 500
        return resp_erro

def buscar_dados_veiculo_e_reserva(placa):
    """Consulta o veículo na Blue Fleet e detecta se é titular ou reserva."""
    placa = placa.strip().upper()
    if len(placa) == 7 and "-" not in placa:
        placa = f"{placa[:3]}-{placa[3:]}"

    try:
        token = get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        
        url_busca = f"{API_URL}/vehicle?LicensePlate={placa}"
        response = get_blindado(url_busca, headers)
        
        if response.status_code == 429:
            return None, None, False, "A Blue Fleet limitou nossas buscas por excesso de requisições. Aguarde 1 minuto e tente novamente."
            
        response.raise_for_status()
        lista_veiculos = response.json().get("data", [])
        if not lista_veiculos:
            return None, None, False, f"Veículo não encontrado para a placa {placa}."

        veiculo_base = lista_veiculos[0]
        carro_titular = None
        carro_reserva = None
        reserva_oculta_detectada = False
        placa_limpa_base = placa.replace("-", "")

        # Lógica 1: Pesquisou o Reserva (Status 14)
        if str(veiculo_base.get("vehicleStatusId")) == "14":
            carro_reserva = veiculo_base

            # 1. Tenta obter o titular diretamente de /vehicle/{vehicleId}/contract-item
            try:
                v_res_id = veiculo_base.get("vehicleId")
                r_vci = get_blindado(f"{API_URL}/vehicle/{v_res_id}/contract-item", headers, rapido=True)
                if r_vci.status_code == 200:
                    for it in r_vci.json().get("data", []):
                        tipo_nome = str(it.get("vehicleTypeName", "")).lower()
                        tipo_id = str(it.get("vehicleTypeId", ""))
                        final_date = it.get("finalDate")
                        placa_tit_cand = it.get("contractLicensePlate")

                        if (tipo_nome == "reserva" or tipo_id == "2") and not final_date and placa_tit_cand:
                            r_tit = get_blindado(f"{API_URL}/vehicle?LicensePlate={placa_tit_cand}", headers)
                            if r_tit.status_code == 200 and r_tit.json().get("data"):
                                carro_titular = r_tit.json().get("data")[0]
                                break
            except Exception:
                pass

            # 2. Fallback: Se não encontrou pelo contract-item, busca pelas ocorrências
            if not carro_titular:
                try:
                    r_ocorrencia = get_blindado(f"{API_URL}/contract-item-request/search?LicensePlate={placa}", headers)
                    if r_ocorrencia.status_code == 200:
                        ocorrencias = r_ocorrencia.json().get("data", [])
                        if ocorrencias:
                            ocorrencias.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
                            for oc in ocorrencias:
                                placa_titular = oc.get("licensePlate")
                                if placa_titular and placa_titular.replace("-", "").upper() != placa_limpa_base:
                                    r_titular = get_blindado(f"{API_URL}/vehicle?LicensePlate={placa_titular}", headers)
                                    if r_titular.status_code == 200 and r_titular.json().get("data"):
                                        carro_titular = r_titular.json().get("data")[0]
                                        break
                except Exception:
                    pass

        # Lógica 2: Pesquisou o Titular
        else:
            carro_titular = veiculo_base
            try:
                r_ocorrencia = get_blindado(f"{API_URL}/contract-item-request/search?LicensePlate={placa}", headers)
                if r_ocorrencia.status_code == 200:
                    ocorrencias = r_ocorrencia.json().get("data", [])
                    ocorrencias.sort(key=lambda x: x.get("createdAt", ""), reverse=True)

                    for oc in ocorrencias[:10]:
                        time.sleep(0.1)
                        req_id = oc.get("contractItemRequestId")
                        if not req_id:
                            continue

                        r_files = get_blindado(f"{API_URL}/contract-item-request/{req_id}/files", headers, rapido=True)
                        if r_files.status_code == 200:
                            arquivos = r_files.json().get("data", r_files.json())
                            if isinstance(arquivos, list):
                                for arquivo in arquivos:
                                    nome_bruto = str(arquivo.get("filename", arquivo.get("fileName", arquivo.get("name", "")))).upper()
                                    match_placa = re.search(r'([A-Z]{3}[ -]?[0-9][A-Z0-9][0-9]{2})', nome_bruto)
                                    if match_placa:
                                        placa_achada = match_placa.group(1).replace("-", "").replace(" ", "")
                                        if placa_achada != placa_limpa_base:
                                            placa_reserva_api = f"{placa_achada[:3]}-{placa_achada[3:]}"
                                            r_reserva = get_blindado(f"{API_URL}/vehicle?LicensePlate={placa_reserva_api}", headers)
                                            if r_reserva.status_code == 200 and r_reserva.json().get("data"):
                                                v_teste = r_reserva.json().get("data")[0]
                                                if str(v_teste.get("vehicleStatusId")) == "14":
                                                    # Validação Dupla: Verifica se este reserva está ativamente alocado para ESTE titular
                                                    v_res_id = v_teste.get("vehicleId")
                                                    r_vci = get_blindado(f"{API_URL}/vehicle/{v_res_id}/contract-item", headers, rapido=True)
                                                    alocado_para_este_titular = False

                                                    if r_vci.status_code == 200:
                                                        for it in r_vci.json().get("data", []):
                                                            p_contrato = str(it.get("contractLicensePlate", "")).replace("-", "").upper()
                                                            tipo_nome = str(it.get("vehicleTypeName", "")).lower()
                                                            tipo_id = str(it.get("vehicleTypeId", ""))
                                                            final_date = it.get("finalDate")

                                                            if p_contrato == placa_limpa_base and (tipo_nome == "reserva" or tipo_id == "2") and not final_date:
                                                                alocado_para_este_titular = True
                                                                break

                                                    if alocado_para_este_titular:
                                                        carro_reserva = v_teste
                                                        reserva_oculta_detectada = False
                                                        break
                                                    else:
                                                        # Foi achada uma placa nos checklists que é reserva, mas não está ativa para este titular
                                                        reserva_oculta_detectada = True

                        if carro_reserva:
                            break
            except Exception as e:
                print(f"Erro na extração do reserva: {e}")

            if not carro_reserva:
                try:
                    if 'ocorrencias' in locals() and ocorrencias:
                        for oc in ocorrencias[:10]:
                            reason_id = str(oc.get("contractItemRequestReasonId", ""))
                            reason_name = str(oc.get("contractItemRequestReasonName", "")).lower()
                            status_name = str(oc.get("contractItemRequestStatusName", "")).lower()
                            step_name = str(oc.get("workflowStepName", "")).lower()
                            if (reason_id == "5" or "reserva" in reason_name or "reserva" in json.dumps(oc).lower()) and ("andamento" in status_name or "aguardando devolu" in step_name):
                                reserva_oculta_detectada = True
                                break
                except Exception:
                    pass

        return carro_titular, carro_reserva, reserva_oculta_detectada, None

    except requests.exceptions.HTTPError as err_http:
        return None, None, False, f"Falha na comunicação com a API: {err_http}"
    except Exception as e:
        return None, None, False, f"Erro interno do sistema: {str(e)}"

def buscar_checklist_pdf_bluefleet(placa):
    """Busca o PDF de checklist arquivado na Blue Fleet para a placa informada."""
    placa = placa.strip().upper()
    placa_limpa = placa.replace("-", "")
    if len(placa) == 7 and "-" not in placa:
        placa = f"{placa[:3]}-{placa[3:]}"

    try:
        token = get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        
        r_ocorrencia = requests.get(f"{API_URL}/contract-item-request/search?LicensePlate={placa}", headers=headers, timeout=10)
        if r_ocorrencia.status_code != 200:
            return None, None, "Erro ao buscar histórico de ocorrências na Blue Fleet."
            
        ocorrencias = r_ocorrencia.json().get("data", [])
        if not ocorrencias:
            return None, None, "Nenhuma ocorrência encontrada para este veículo."
            
        ocorrencias.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        
        for oc in ocorrencias:
            req_id = oc.get("contractItemRequestId")
            if not req_id:
                continue
                
            url_arquivos = f"{API_URL}/contract-item-request/{req_id}/files"
            r_files = requests.get(url_arquivos, headers=headers, timeout=10)
            if r_files.status_code == 200:
                lista_arquivos = r_files.json().get("data", r_files.json())
                if isinstance(lista_arquivos, list):
                    for arquivo in lista_arquivos:
                        nome_bruto = str(arquivo.get("filename", arquivo.get("fileName", arquivo.get("name", "")))).upper()
                        padrao_checklist = rf"^{placa_limpa}-\d{{2}}\.\d{{2}}\.\d{{2}}"
                        
                        if re.search(padrao_checklist, nome_bruto) and placa_limpa != "":
                            id_arquivo = arquivo.get("id", arquivo.get("fileId"))
                            url_direta = arquivo.get("url")
                            
                            if url_direta:
                                r_pdf = requests.get(url_direta, timeout=15)
                            else:
                                url_api = f"{API_URL}/contract-item-request/{req_id}/files/{id_arquivo}"
                                r_pdf = requests.get(url_api, headers=headers, timeout=15)
                            
                            if r_pdf.status_code == 200:
                                return r_pdf.content, f"Checklist_{placa_limpa}.pdf", None
                                
        return None, None, f"Checklist não encontrado para a placa {placa}."
    except Exception as e:
        return None, None, f"Erro interno ao buscar checklist: {str(e)}"

def obter_ultimos_checklists(placa, limite=3):
    """Localiza os últimos 'limite' checklists oficiais de uma placa nas ocorrências."""
    placa = placa.strip().upper()
    placa_limpa = placa.replace("-", "").replace(" ", "")
    placa_api = f"{placa_limpa[:3]}-{placa_limpa[3:]}" if len(placa_limpa) == 7 else placa
    placa_re = rf"{placa_limpa[:3]}-?{placa_limpa[3:]}" if len(placa_limpa) == 7 else placa_limpa
    padrao_checklist = rf"^{placa_re}-(\d{{2}})\.(\d{{2}})\.(\d{{2}})(?:-([A-Za-z0-9_]+))?"

    checklists_encontrados = []
    try:
        token = get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        
        r_oc = get_blindado(f"{API_URL}/contract-item-request/search?LicensePlate={placa_api}", headers)
        if r_oc.status_code != 200:
            return []
            
        ocorrencias = r_oc.json().get("data", [])
        if not ocorrencias:
            return []
            
        ocorrencias.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        
        for oc in ocorrencias[:15]:
            req_id = oc.get("contractItemRequestId")
            if not req_id:
                continue
                
            origem = oc.get("contractItemRequestReasonName") or "Manutenção"
            r_files = get_blindado(f"{API_URL}/contract-item-request/{req_id}/files", headers, rapido=True)
            if r_files.status_code == 200:
                arquivos = r_files.json().get("data", r_files.json())
                if isinstance(arquivos, list):
                    for arq in arquivos:
                        nome = str(arq.get("filename") or arq.get("fileName") or arq.get("name") or "").upper().strip()
                        if not nome.endswith(".PDF"):
                            continue
                        # Padrão estrito da Nevada: PLACA-DD.MM.YY...
                        m = re.search(padrao_checklist, nome)
                        if m:
                            file_id = arq.get("id") or arq.get("fileId")
                            if any(c["file_id"] == file_id for c in checklists_encontrados):
                                continue
                            dia, mes, ano = m.group(1), m.group(2), m.group(3)
                            sufixo = (m.group(4) or "").upper()
                            if "E" in sufixo and "S" in sufixo:
                                tipo = "Entrada e Saída"
                            elif "S" in sufixo:
                                tipo = "Saída"
                            elif "E" in sufixo:
                                tipo = "Entrada"
                            else:
                                tipo = "Vistoria"
                                
                            data_formatada = f"{dia}/{mes}/20{ano}"
                            
                            checklists_encontrados.append({
                                "req_id": req_id,
                                "file_id": file_id,
                                "nome_arquivo": nome,
                                "tipo": tipo,
                                "data": data_formatada,
                                "origem": origem
                            })
                            
                            if len(checklists_encontrados) >= limite:
                                break
            if len(checklists_encontrados) >= limite:
                break
                
        return checklists_encontrados
    except Exception as e:
        print(f"Erro ao listar checklists da placa {placa}: {e}")
        return []

def baixar_arquivo_checklist_stream(req_id, file_id):
    """Baixa o arquivo em memória da Blue Fleet sem salvar nada em disco."""
    try:
        token = get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        
        r_files = requests.get(f"{API_URL}/contract-item-request/{req_id}/files", headers=headers, timeout=10)
        url_direta = None
        nome_arquivo = f"Checklist_{req_id}_{file_id}.pdf"
        
        if r_files.status_code == 200:
            arquivos = r_files.json().get("data", r_files.json())
            if isinstance(arquivos, list):
                for arq in arquivos:
                    fid = str(arq.get("id") or arq.get("fileId"))
                    if fid == str(file_id):
                        url_direta = arq.get("url")
                        nome_arquivo = arq.get("filename") or arq.get("fileName") or arq.get("name") or nome_arquivo
                        break
                        
        if url_direta:
            r_pdf = requests.get(url_direta, timeout=15)
        else:
            url_api = f"{API_URL}/contract-item-request/{req_id}/files/{file_id}"
            r_pdf = requests.get(url_api, headers=headers, timeout=15)
            
        if r_pdf.status_code == 200:
            return r_pdf.content, nome_arquivo, None
        return None, None, "Arquivo não encontrado na Blue Fleet."
    except Exception as e:
        return None, None, f"Erro ao baixar arquivo: {e}"

def enriquecer_dados_veiculo(placa_limpa):
    """Busca modelo, montadora, cliente, contrato e contractItemId da placa."""
    resultado = {
        "modelo_veiculo": "",
        "montadora": "",
        "cliente_nome": "",
        "contrato_id": None,
        "contract_item_id": None
    }
    try:
        token = get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        placa_api = f"{placa_limpa[:3]}-{placa_limpa[3:]}" if len(placa_limpa) == 7 else placa_limpa

        r_veic = requests.get(f"{API_URL}/vehicle?LicensePlate={placa_api}", headers=headers, timeout=5)
        if r_veic.status_code == 200 and r_veic.json().get("data"):
            veic = r_veic.json()["data"][0]
            resultado["modelo_veiculo"] = veic.get("vehicleModelName", "")
            resultado["montadora"] = veic.get("automakerName", "")
            veiculo_id = veic.get("vehicleId")

            contrato = veic.get("contract") or {}
            resultado["contrato_id"] = contrato.get("contractId")
            resultado["cliente_nome"] = (contrato.get("customer") or {}).get("tradingName", "")

            if resultado["contrato_id"]:
                r_items = requests.get(f"{API_URL}/contract/{resultado['contrato_id']}/items", headers=headers, timeout=5)
                if r_items.status_code == 200:
                    itens = r_items.json().get("data", r_items.json())
                    if isinstance(itens, list):
                        for it in itens:
                            if it.get("vehicleId") == veiculo_id:
                                resultado["contract_item_id"] = it.get("contractItemId") or it.get("id")
                                break
    except Exception as e:
        print(f"Aviso: Não foi possível enriquecer dados da placa {placa_limpa}: {e}")
    return resultado

def resolver_contract_item_id(placa_api, headers=None):
    """Localiza o contractItemId ATIVO prioritário para a placa informada."""
    if headers is None:
        token = get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    # Estratégia 1: Busca o contrato ativo vinculado diretamente ao veículo
    r_veic = requests.get(f"{API_URL}/vehicle?LicensePlate={placa_api}", headers=headers, timeout=5)
    if r_veic.status_code == 200:
        veiculos = r_veic.json().get("data", [])
        if veiculos:
            contrato = veiculos[0].get("contract") or {}
            contrato_id = contrato.get("contractId")
            veiculo_id = veiculos[0].get("vehicleId")
            if contrato_id:
                r_items = requests.get(f"{API_URL}/contract/{contrato_id}/items", headers=headers, timeout=5)
                if r_items.status_code == 200:
                    itens = r_items.json().get("data", r_items.json())
                    if isinstance(itens, list):
                        for it in itens:
                            if it.get("vehicleId") == veiculo_id:
                                return it.get("contractItemId") or it.get("id")

    # Estratégia 2: Busca por itens de contrato ativos em /contract-item/search
    r_ci = requests.get(f"{API_URL}/contract-item/search?LicensePlate={placa_api}", headers=headers, timeout=5)
    if r_ci.status_code == 200:
        itens_ci = r_ci.json().get("data", [])
        for it in itens_ci:
            status_nome = str(it.get("contractItemStatusName", "")).lower()
            if "andamento" in status_nome or str(it.get("contractItemStatusId")) == "1":
                return it.get("contractItemId")
        if itens_ci:
            return itens_ci[0].get("contractItemId")

    # Estratégia 3: Ocorrências recentes (último recurso)
    r_oc = requests.get(f"{API_URL}/contract-item-request/search?LicensePlate={placa_api}", headers=headers, timeout=5)
    if r_oc.status_code == 200:
        for oc in r_oc.json().get("data", []):
            if oc.get("contractItemId"):
                return oc.get("contractItemId")

    return None

def criar_ocorrencia_e_avancar_fila(contract_item_id, payload_bluefleet, data_iso):
    """Cria a ocorrência na Blue Fleet e avança a esteira até Aguardando Orçamento."""
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    url_criacao = f"{API_URL}/contract-item/{contract_item_id}/contract-item-request"
    resp_bf = requests.post(url_criacao, headers=headers, json=payload_bluefleet, timeout=15)
    
    if resp_bf.status_code in [200, 201]:
        resp_dados = resp_bf.json()
        req_id = resp_dados.get("data", {}).get("contractItemRequestId")
        
        # Avanço Automático do Workflow até Aguardando Orçamento (3 passos)
        try:
            requests.post(f"{API_URL}/contract-item-request/{req_id}/pre-scheduling", 
                          headers=headers, json={"providerId": 197, "scheduledFor": data_iso}, timeout=5)
            requests.post(f"{API_URL}/contract-item-request/{req_id}/schedule-confirmation", 
                          headers=headers, timeout=5)
            r_chk = requests.post(f"{API_URL}/contract-item-request/{req_id}/waiting-checkin", 
                                  headers=headers, json={"confirmVehicleCheckinDate": data_iso}, timeout=5)
            print(f"Transição de fila concluída para ocorrência {req_id} (Status {r_chk.status_code})")
        except Exception as e_chk:
            print(f"Aviso: Falha na transição automática de fila: {e_chk}")

        return True, req_id, None
    else:
        return False, None, resp_bf.text
