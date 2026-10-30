import os
import re
import json
import time
import base64
import threading
import requests
from datetime import datetime, timezone, timedelta

CLIENT_ID = os.getenv("BLUEFLEET_CLIENT_ID")
CLIENT_SECRET = os.getenv("BLUEFLEET_CLIENT_SECRET")
AUTH_URL = "https://auth.bluefleet.com.br/connect/token"
API_URL = "https://api.bluefleet.com.br"

_TOKEN_CACHE = {"token": None, "expires_at": 0}
_token_lock = threading.Lock()

def get_access_token():
    """Obtém o token de acesso da Blue Fleet com cache em memória seguro (thread-safe)."""
    now = time.time()
    # Retorna do cache em memória se ainda for válido por pelo menos mais 60 segundos
    if _TOKEN_CACHE["token"] and now < (_TOKEN_CACHE["expires_at"] - 60):
        return _TOKEN_CACHE["token"]

    with _token_lock:
        now = time.time()
        if _TOKEN_CACHE["token"] and now < (_TOKEN_CACHE["expires_at"] - 60):
            return _TOKEN_CACHE["token"]

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
        res_json = response.json()
        token = res_json.get("access_token")
        expires_in = int(res_json.get("expires_in") or 3600)

        _TOKEN_CACHE["token"] = token
        _TOKEN_CACHE["expires_at"] = now + expires_in
        return token

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
            return None, None, False, [], "A Blue Fleet limitou nossas buscas por excesso de requisições. Aguarde 1 minuto e tente novamente."
            
        response.raise_for_status()
        lista_veiculos = response.json().get("data", [])
        if not lista_veiculos:
            return None, None, False, [], f"Veículo não encontrado para a placa {placa}."

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

                    # Filtra APENAS ocorrências ativas (não concluídas e não canceladas) para buscar carro reserva em andamento
                    ocorrencias_ativas = [
                        oc for oc in ocorrencias
                        if str(oc.get("contractItemRequestStatusName", "")).strip().lower() not in ["concluída", "concluida", "cancelada"]
                    ]

                    # Padrão estrito de placa brasileira: ABC-1234 ou ABC1D23 (Mercosul), evitando casar palavras como 'IMAGE 2026'
                    padrao_placa = re.compile(r'(?<![A-Z0-9])([A-Z]{3}-?(?:[0-9]{4}|[0-9][A-Z][0-9]{2}))(?![A-Z0-9])', re.IGNORECASE)

                    for oc in ocorrencias_ativas[:5]:
                        req_id = oc.get("contractItemRequestId")
                        if not req_id:
                            continue

                        r_files = get_blindado(f"{API_URL}/contract-item-request/{req_id}/files", headers, rapido=True)
                        if r_files.status_code == 200:
                            arquivos = r_files.json().get("data", r_files.json())
                            if isinstance(arquivos, list):
                                for arquivo in arquivos:
                                    nome_bruto = str(arquivo.get("filename", arquivo.get("fileName", arquivo.get("name", "")))).upper()
                                    match_placa = padrao_placa.search(nome_bruto)
                                    if match_placa:
                                        placa_achada = match_placa.group(1).replace("-", "").replace(" ", "")
                                        if placa_achada != placa_limpa_base:
                                            placa_reserva_api = f"{placa_achada[:3]}-{placa_achada[3:]}"
                                            r_reserva = get_blindado(f"{API_URL}/vehicle?LicensePlate={placa_reserva_api}", headers, rapido=True)
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

                        if carro_reserva:
                            break

                    # Se após checar as ocorrências ATIVAS ainda não achou o veículo reserva mas alguma ATIVA é de motivo Carro Reserva
                    if not carro_reserva and ocorrencias_ativas:
                        for oc in ocorrencias_ativas[:5]:
                            reason_id = str(oc.get("contractItemRequestReasonId", ""))
                            reason_name = str(oc.get("contractItemRequestReasonName", "")).lower()
                            step_name = str(oc.get("workflowStepName", "")).lower()
                            if reason_id == "5" or "reserva" in reason_name or "aguardando devolu" in step_name:
                                reserva_oculta_detectada = True
                                break
            except Exception as e:
                print(f"Erro na extração do reserva: {e}")

        # Busca avisos e políticas de frota vinculados ao contrato
        contract_id = None
        model_id = None
        if carro_titular:
            contract_id = (carro_titular.get("contract") or {}).get("contractId") or carro_titular.get("contractId")
            model_id = carro_titular.get("vehicleModelId")
        if not contract_id and carro_reserva:
            contract_id = (carro_reserva.get("contract") or {}).get("contractId") or carro_reserva.get("contractId")
            model_id = carro_reserva.get("vehicleModelId")
        if not contract_id and veiculo_base:
            contract_id = (veiculo_base.get("contract") or {}).get("contractId") or veiculo_base.get("contractId")
            model_id = veiculo_base.get("vehicleModelId")

        politicas_frota = buscar_politicas_contrato(contract_id, model_id) if contract_id else []

        # Busca última revisão por quilometragem / tempo para o titular e reserva
        if carro_titular:
            placa_t = carro_titular.get("licensePlate") or placa
            oc_t = ocorrencias if ('ocorrencias' in locals() and ocorrencias and placa_t.replace("-", "") == placa_limpa_base) else None
            carro_titular["revisao"] = obter_ultima_revisao(placa_t, oc_t) or "Não informada"

        if carro_reserva:
            placa_r = carro_reserva.get("licensePlate")
            if placa_r:
                oc_r = ocorrencias if ('ocorrencias' in locals() and ocorrencias and placa_r.replace("-", "") == placa_limpa_base) else None
                carro_reserva["revisao"] = obter_ultima_revisao(placa_r, oc_r) or "Não informada"

        return carro_titular, carro_reserva, reserva_oculta_detectada, politicas_frota, None

    except requests.exceptions.HTTPError as err_http:
        return None, None, False, [], f"Falha na comunicação com a API: {err_http}"
    except Exception as e:
        return None, None, False, [], f"Erro interno do sistema: {str(e)}"

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
                        placa_re = rf"{placa_limpa[:3]}-?{placa_limpa[3:]}" if len(placa_limpa) == 7 else placa_limpa
                        padrao_checklist = rf"^{placa_re}-\d{{2}}\.\d{{2}}\.\d{{2}}"
                        
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

def buscar_politicas_contrato(contract_id, vehicle_model_id=None):
    """Busca as políticas/avisos cadastrados no contrato (ex: sem direito a manutenção/reserva)."""
    if not contract_id:
        return []
        
    try:
        token = get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        
        r = get_blindado(f"{API_URL}/commercial/contract/{contract_id}", headers, rapido=True)
        if r.status_code != 200:
            return []
            
        cdata = r.json().get("data", {})
        politicas_brutas = []
        
        # 1. Políticas genéricas do contrato
        if cdata.get("genericPolicies"):
            politicas_brutas.extend(cdata.get("genericPolicies"))
            
        # 2. Políticas específicas dos veículos do contrato
        veiculos_contrato = cdata.get("vehicles", [])
        if vehicle_model_id:
            for v in veiculos_contrato:
                if v.get("vehicleModelId") == vehicle_model_id:
                    politicas_brutas.extend(v.get("policies") or [])
                    
        # Se não achou por modelo específico, traz de todos os veículos
        if not politicas_brutas:
            for v in veiculos_contrato:
                politicas_brutas.extend(v.get("policies") or [])

        secoes_trad = {
            "general": "Geral",
            "maintenance": "Manutenção",
            "claim": "Sinistro",
            "tire": "Pneus",
            "trafficticket": "Multas",
            "replacement": "Carro Reserva"
        }
        
        tipos_trad = {
            "important": "Importante",
            "warning": "Atenção",
            "information": "Informativo"
        }

        politicas_formatadas = []
        descricoes_vistas = set()

        for p in politicas_brutas:
            if not isinstance(p, dict):
                continue
            desc = (p.get("description") or "").strip()
            if not desc or desc in descricoes_vistas:
                continue
                
            descricoes_vistas.add(desc)
            
            sec_raw = str(p.get("policySection", "General")).lower()
            tipo_raw = str(p.get("policyType", "Important")).lower()
            
            secao = secoes_trad.get(sec_raw, p.get("policySection") or "Geral")
            tipo = tipos_trad.get(tipo_raw, p.get("policyType") or "Importante")
            
            politicas_formatadas.append({
                "secao": secao,
                "tipo": tipo,
                "descricao": desc,
                "showPopup": p.get("showPopup", True)
            })

        return politicas_formatadas
    except Exception as e:
        print(f"Erro ao buscar políticas do contrato {contract_id}: {e}")
        return []

def obter_ultima_revisao(placa, ocorrencias=None):
    """Localiza a última ocorrência de revisão por quilometragem / tempo e retorna o KM formatado."""
    placa = placa.strip().upper()
    placa_limpa = placa.replace("-", "").replace(" ", "")
    placa_api = f"{placa_limpa[:3]}-{placa_limpa[3:]}" if len(placa_limpa) == 7 else placa

    try:
        token = get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        if ocorrencias is None:
            r_oc = get_blindado(f"{API_URL}/contract-item-request/search?LicensePlate={placa_api}", headers, rapido=True)
            if r_oc.status_code == 200:
                ocorrencias = r_oc.json().get("data", [])
            else:
                ocorrencias = []

        if not ocorrencias:
            return None

        ocorrencias_ordenadas = sorted(ocorrencias, key=lambda x: x.get("createdAt", ""), reverse=True)

        for oc in ocorrencias_ordenadas:
            cid = oc.get("contractItemRequestReasonClassId")
            rid = oc.get("contractItemRequestReasonId")
            rname = str(oc.get("contractItemRequestReasonName") or "").lower()

            # ClassId 1 = Revisão por Quilometragem, rid == 1 = Manutenção Preventiva, ou menção a revisão
            if cid in [1, 2] or rid == 1 or "revis" in rname:
                req_id = oc.get("contractItemRequestId")
                if not req_id:
                    continue

                r_det = get_blindado(f"{API_URL}/contract-item-request/{req_id}", headers, rapido=True)
                if r_det.status_code == 200:
                    det = r_det.json().get("data", {})
                    cname = str(det.get("contractItemRequestReasonClassName") or "").lower()
                    maint = det.get("maintenance") or {}
                    desc = str(maint.get("description") or "").lower()

                    is_revisao = (
                        det.get("contractItemRequestReasonClassId") in [1, 2] or
                        "revis" in cname or
                        "tempo" in cname or
                        "quilometragem" in cname or
                        "revis" in desc
                    )

                    if is_revisao:
                        km = det.get("currentOdometer") or maint.get("currentOdometer")
                        if km:
                            try:
                                km_limpo = re.sub(r"[^\d]", "", str(km))
                                km_num = int(km_limpo)
                                return f"{km_num:,}".replace(",", ".") + " km"
                            except Exception:
                                return f"{km} km"

        return None
    except Exception as e:
        print(f"Erro ao obter última revisão para placa {placa}: {e}")
        return None

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
            dt_schedule = data_iso or (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT10:00:00")
            dt_checkin = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

            r_pre = requests.post(f"{API_URL}/contract-item-request/{req_id}/pre-scheduling", 
                                  headers=headers, json={"providerId": 197, "scheduledFor": dt_schedule}, timeout=5)
            r_cnf = requests.post(f"{API_URL}/contract-item-request/{req_id}/schedule-confirmation", 
                                  headers=headers, timeout=5)
            r_chk = requests.post(f"{API_URL}/contract-item-request/{req_id}/waiting-checkin", 
                                  headers=headers, json={"confirmVehicleCheckinDate": dt_checkin}, timeout=5)
            print(f"Transição de fila para ocorrência {req_id}: pre={r_pre.status_code}, cnf={r_cnf.status_code}, chk={r_chk.status_code}")
            if r_chk.status_code not in [200, 201]:
                print(f"Aviso retorno waiting-checkin: {r_chk.text}")
        except Exception as e_chk:
            print(f"Aviso: Falha na transição automática de fila: {e_chk}")

        return True, req_id, None
    else:
        return False, None, resp_bf.text

def lancar_ordem_servico_pecas(contract_item_request_id, km_atual, itens_pecas, provider_id=197):
    """
    Lança a Ordem de Serviço de Peças na Blue Fleet com os itens requisitados
    (Padrão Nevada: Peças, Padrão, Produto e Despesa).
    Retorna (sucesso, purchase_order_number, os_number, erro).
    """
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    try:
        km_limpo = int(re.sub(r"[^\d]", "", str(km_atual or 0)))
    except Exception:
        km_limpo = 0

    order_items_payload = []
    for item in itens_pecas:
        try:
            qtd = float(item.get("quantidade", 1))
        except Exception:
            qtd = 1.0

        try:
            unit = float(item.get("valor_unitario", 0))
        except Exception:
            unit = 0.0

        desc = str(item.get("descricao_pura") or item.get("descricao", "")).strip().upper()
        desc = re.sub(r"\s*:\s*R\$\s*[\d\.,]+.*$", "", desc, flags=re.IGNORECASE).strip()
        desc = re.sub(r"\s*\$\s*[\d\.,]+.*$", "", desc).strip()

        order_items_payload.append({
            "expenseGroupId": 7,                              # 01.01.02 - Peças
            "orderItemClassId": 1,                            # Default (PADRÃO)
            "contractItemRequestOrderItemPartTypeId": 2,      # Part (PRODUTO)
            "expenseTypeId": 1,                               # Expense (DESPESA)
            "quantity": qtd,
            "unitPrice": unit,
            "discount": 0.0,
            "description": desc
        })

    if not order_items_payload:
        return False, None, None, "Nenhum item de peça para lançar."

    payload = {
        "providerId": provider_id,
        "odometer": km_limpo,
        "orderItems": order_items_payload
    }

    url_order = f"{API_URL}/contract-item-request/{contract_item_request_id}/order"
    resp = requests.post(url_order, headers=headers, json=payload, timeout=15)

    if resp.status_code in [200, 201]:
        dados_resp = resp.json().get("data", resp.json())
        ord_id = dados_resp.get("contractItemRequestOrderId") or dados_resp.get("id")
        os_number = dados_resp.get("orderNumber")
        oc_number = dados_resp.get("purchaseOrderNumber")

        # Se o número da O.C ainda não veio diretamente, aprova os itens da OS para gerar a Ordem de Compra oficial
        if not oc_number and ord_id:
            try:
                # 1. Busca os IDs dos itens da OS recém-criada
                r_det = requests.get(f"{API_URL}/contract-item-request/{contract_item_request_id}/order/{ord_id}", headers=headers, timeout=10)
                if r_det.status_code == 200:
                    det = r_det.json().get("data", {})
                    itens_os = det.get("orderItems", [])
                    itens_aprovacao = [
                        {
                            "contractItemRequestOrderItemId": it.get("orderItemId"),
                            "orderApprovalCustomerItemStatusId": "Approved"
                        }
                        for it in itens_os if it.get("orderItemId")
                    ]

                    # 2. Dispara a aprovação dos itens para a Blue Fleet gerar a O.C
                    if itens_aprovacao:
                        payload_aprovacao = {
                            "comments": "Aprovado automaticamente - Peças Nevada",
                            "items": itens_aprovacao
                        }
                        url_app = f"{API_URL}/contract-item-request/{contract_item_request_id}/order/{ord_id}/approval"
                        requests.post(url_app, headers=headers, json=payload_aprovacao, timeout=10)

                # 3. Consulta os detalhes atualizados da OS com a O.C gerada
                time.sleep(1.5)
                r_pos = requests.get(f"{API_URL}/contract-item-request/{contract_item_request_id}/order/{ord_id}", headers=headers, timeout=10)
                if r_pos.status_code == 200:
                    det_pos = r_pos.json().get("data", {})
                    oc_number = det_pos.get("purchaseOrderNumber") or oc_number
                    os_number = det_pos.get("orderNumber") or os_number
            except Exception as e_app:
                print(f"Aviso ao aprovar OS {ord_id} e obter Ordem de Compra: {e_app}")

        return True, oc_number, os_number, None
    else:
        return False, None, None, resp.text

def anexar_arquivo_ocorrencia(contract_item_request_id, nome_arquivo, pdf_bytes):
    """Anexa um documento PDF diretamente na ocorrência da Blue Fleet."""
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    b64_doc = base64.b64encode(pdf_bytes).decode("utf-8")
    payload = {
        "fileName": nome_arquivo,
        "base64Document": b64_doc
    }

    url_file = f"{API_URL}/contract-item-request/{contract_item_request_id}/file"
    resp = requests.post(url_file, headers=headers, json=payload, timeout=15)
    return resp.status_code in [200, 201], resp.text

def formatar_data_br(data_iso):
    """Converte ISO timestamp ou string de data para DD/MM/AAAA."""
    if not data_iso:
        return "-"
    try:
        clean = str(data_iso).replace("Z", "")
        if "T" in clean:
            dt = datetime.fromisoformat(clean)
            return dt.strftime("%d/%m/%Y")
        elif "-" in clean:
            partes = clean.split("-")
            if len(partes) >= 3:
                return f"{partes[2][:2]}/{partes[1]}/{partes[0]}"
    except Exception:
        pass
    return str(data_iso)[:10]

_OFICINA_CACHE = {}
_OFICINA_CACHE_LOCK = threading.Lock()

def consultar_historico_pecas_veiculo(placa):
    """Consulta todas as peças e serviços já realizados no veículo na Blue Fleet de forma robusta e com cache."""
    placa_raw = (placa or "").strip().upper()
    placa_limpa = placa_raw.replace("-", "").replace(" ", "")
    placa_com_hifen = f"{placa_limpa[:3]}-{placa_limpa[3:]}" if len(placa_limpa) == 7 else placa_limpa

    # Verifica cache em memória (TTL: 90 segundos para respostas rápidas e sem flutuação)
    now = time.time()
    cache_key = placa_limpa
    with _OFICINA_CACHE_LOCK:
        if cache_key in _OFICINA_CACHE:
            cached_data, cached_time = _OFICINA_CACHE[cache_key]
            if now - cached_time < 90:
                return cached_data["dados_veiculo"], cached_data["pecas"], cached_data["erro"]

    try:
        token = get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        # 1. Busca dados básicos do veículo (modelo, km atual)
        dados_veiculo = {"placa": placa_com_hifen, "modelo": "", "km_atual": ""}
        url_busca = f"{API_URL}/vehicle?LicensePlate={placa_com_hifen}"
        resp_veh = get_blindado(url_busca, headers, rapido=True)
        if resp_veh.status_code == 200:
            veiculos = resp_veh.json().get("data", [])
            if veiculos:
                v = veiculos[0]
                dados_veiculo["modelo"] = v.get("vehicleModelName") or v.get("modelName") or ""
                km_val = v.get("currentOdometer") or v.get("odometer")
                if km_val:
                    try:
                        dados_veiculo["km_atual"] = f"{int(km_val):,} km".replace(",", ".")
                    except Exception:
                        dados_veiculo["km_atual"] = f"{km_val} km"

        # 2. Busca histórico de ocorrências do veículo (tenta com hífen e fallback sem hífen se vazio)
        url_oc = f"{API_URL}/contract-item-request/search?LicensePlate={placa_com_hifen}"
        resp_oc = get_blindado(url_oc, headers)
        ocorrencias = []
        if resp_oc.status_code == 200:
            ocorrencias = resp_oc.json().get("data", [])
        
        if not ocorrencias and placa_limpa != placa_com_hifen:
            resp_oc_limpa = get_blindado(f"{API_URL}/contract-item-request/search?LicensePlate={placa_limpa}", headers)
            if resp_oc_limpa.status_code == 200:
                ocorrencias = resp_oc_limpa.json().get("data", [])

        if not ocorrencias and resp_oc.status_code != 200:
            return dados_veiculo, [], "Não foi possível consultar as ocorrências do veículo na Blue Fleet."

        if not ocorrencias:
            resultado = (dados_veiculo, [], None)
            with _OFICINA_CACHE_LOCK:
                _OFICINA_CACHE[cache_key] = ({"dados_veiculo": dados_veiculo, "pecas": [], "erro": None}, now)
            return resultado

        # Exclui apenas ocorrências que notoriamente nunca possuem O.S de manutenção (4: Infração, 6: Devolução)
        candidatas = [
            oc for oc in ocorrencias 
            if oc.get("contractItemRequestReasonId") not in [4, 6]
        ]

        if not candidatas:
            resultado = (dados_veiculo, [], None)
            with _OFICINA_CACHE_LOCK:
                _OFICINA_CACHE[cache_key] = ({"dados_veiculo": dados_veiculo, "pecas": [], "erro": None}, now)
            return resultado

        def extrair_pecas_ocorrencia(oc):
            req_id = oc.get("contractItemRequestId")
            if not req_id:
                return []
            
            # Utiliza get_blindado com retry automático para não perder requisições por rate limit (429)
            r_det = get_blindado(f"{API_URL}/contract-item-request/{req_id}", headers)
            if r_det.status_code != 200:
                return []
            det = r_det.json().get("data", {})
            orders = det.get("contractItemRequestOrders", [])
            itens_pecas = []

            for ord_info in orders:
                ord_id = ord_info.get("contractItemRequestOrderId")
                if not ord_id:
                    continue
                r_ord = get_blindado(f"{API_URL}/contract-item-request/{req_id}/order/{ord_id}", headers)
                if r_ord.status_code != 200:
                    continue
                order_data = r_ord.json().get("data", {})
                km_raw = order_data.get("odometer") or det.get("currentOdometer")
                try:
                    km_num = int(km_raw) if km_raw else 0
                    km_fmt = f"{km_num:,} km".replace(",", ".") if km_raw else "-"
                except Exception:
                    km_fmt = f"{km_raw} km" if km_raw else "-"
                    km_num = 0

                dt_raw = order_data.get("createdAt") or det.get("createdAt") or ""
                dt_fmt = formatar_data_br(dt_raw)

                for item in order_data.get("orderItems", []):
                    nome_peca = (item.get("partName") or item.get("description") or "").strip()
                    if not nome_peca:
                        continue
                    
                    qtd = item.get("quantity")
                    nome_exibicao = nome_peca
                    if qtd is not None:
                        try:
                            qtd_val = float(qtd)
                            if qtd_val not in [0.0, 1.0]:
                                qtd_str = f"{qtd_val:g}"
                                nome_exibicao = f"{nome_peca} ({qtd_str})"
                        except Exception:
                            pass

                    tipo_raw = str(item.get("contractItemRequestOrderItemPartTypeName") or "").upper()
                    tipo_fmt = "Peça" if tipo_raw in ["PRODUTO", "PEÇA", "PECA", "PART"] else "Serviço"

                    itens_pecas.append({
                        "item": nome_exibicao,
                        "tipo": tipo_fmt,
                        "data": dt_fmt,
                        "km": km_fmt,
                        "_sort_dt": dt_raw,
                        "_sort_km": km_num
                    })
            return itens_pecas

        todas_pecas = []
        from concurrent.futures import ThreadPoolExecutor
        # Limita para 3 workers para evitar rate limit simultâneo da API da Blue Fleet
        with ThreadPoolExecutor(max_workers=3) as executor:
            resultados = executor.map(extrair_pecas_ocorrencia, candidatas)
            for res in resultados:
                todas_pecas.extend(res)

        # Ordena as peças pela data e KM mais recentes primeiro
        todas_pecas.sort(key=lambda x: (x.get("_sort_dt", ""), x.get("_sort_km", 0)), reverse=True)

        # Se o km_atual não veio da consulta veicular, utiliza o maior KM encontrado nas manutenções
        if not dados_veiculo.get("km_atual"):
            max_km = max((p.get("_sort_km", 0) for p in todas_pecas), default=0)
            if max_km > 0:
                dados_veiculo["km_atual"] = f"{max_km:,} km".replace(",", ".")

        # Salva em cache
        with _OFICINA_CACHE_LOCK:
            _OFICINA_CACHE[cache_key] = ({"dados_veiculo": dados_veiculo, "pecas": todas_pecas, "erro": None}, now)

        return dados_veiculo, todas_pecas, None

    except Exception as e:
        print(f"Erro ao consultar histórico de peças para {placa_raw}: {e}")
        return {"placa": placa_com_hifen, "modelo": "", "km_atual": ""}, [], f"Erro interno ao consultar: {str(e)}"

