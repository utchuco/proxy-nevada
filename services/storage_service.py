import os
import json
import base64

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKLISTS_FILE = os.path.join(BASE_DIR, "checklists_pendentes.json")
CACHE_PLACAS_FILE = os.path.join(BASE_DIR, "placas_cache.json")
PASTA_CHECKLISTS_PDF = os.path.join(BASE_DIR, "documentos", "checklists_recebidos")

def obter_checklists_pendentes():
    if not os.path.exists(CHECKLISTS_FILE):
        return []
    try:
        with open(CHECKLISTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def salvar_novo_checklist_pendente(novo_pendente):
    fila = obter_checklists_pendentes()
    fila.append(novo_pendente)
    with open(CHECKLISTS_FILE, "w", encoding="utf-8") as f:
        json.dump(fila, f, ensure_ascii=False, indent=4)
    return novo_pendente

def obter_checklist_por_id(id_aprovacao):
    fila = obter_checklists_pendentes()
    for item in fila:
        if item.get("id_aprovacao") == id_aprovacao:
            return item
    return None

def remover_checklist_pendente(id_aprovacao):
    fila = obter_checklists_pendentes()
    nova_fila = [item for item in fila if item.get("id_aprovacao") != id_aprovacao]
    with open(CHECKLISTS_FILE, "w", encoding="utf-8") as f:
        json.dump(nova_fila, f, ensure_ascii=False, indent=4)
    return True

def atualizar_checklist_pendente(id_aprovacao, updates):
    fila = obter_checklists_pendentes()
    atualizado = False
    for i, item in enumerate(fila):
        if item.get("id_aprovacao") == id_aprovacao:
            fila[i].update(updates)
            atualizado = True
            break
    if atualizado:
        with open(CHECKLISTS_FILE, "w", encoding="utf-8") as f:
            json.dump(fila, f, ensure_ascii=False, indent=4)
    return atualizado

def salvar_pdf_checklist(placa_limpa, id_aprovacao, pdf_base64):
    if not pdf_base64:
        return None
    try:
        os.makedirs(PASTA_CHECKLISTS_PDF, exist_ok=True)
        nome_arquivo = f"Checklist_{placa_limpa}_{id_aprovacao[:8]}.pdf"
        caminho_completo = os.path.join(PASTA_CHECKLISTS_PDF, nome_arquivo)

        if "," in pdf_base64:
            pdf_base64 = pdf_base64.split(",")[1]

        with open(caminho_completo, "wb") as f_pdf:
            f_pdf.write(base64.b64decode(pdf_base64))
        return caminho_completo
    except Exception as e:
        print(f"Erro ao salvar PDF em disco: {e}")
        return None

def obter_pdf_checklist_path(id_aprovacao):
    item = obter_checklist_por_id(id_aprovacao)
    if item and item.get("caminho_pdf"):
        caminho = item.get("caminho_pdf")
        if os.path.exists(caminho):
            return caminho
    return None

def buscar_crlv_path(placa):
    placa_limpa = placa.replace("-", "").replace(" ", "").upper()
    pastas_busca = [
        os.path.join(BASE_DIR, "documentos", "2026"),
        os.path.join(BASE_DIR, "documentos", "2025")
    ]
    for pasta in pastas_busca:
        if os.path.exists(pasta):
            for raiz, subpastas, arquivos in os.walk(pasta):
                for arquivo in arquivos:
                    nome_arquivo_limpo = arquivo.replace("-", "").replace(" ", "").upper()
                    if placa_limpa in nome_arquivo_limpo and arquivo.upper().endswith(".PDF"):
                        return os.path.join(raiz, arquivo)
    return None

def buscar_sugestoes_placas(termo_busca, limite=50):
    termo = termo_busca.strip().upper().replace("-", "")
    if len(termo) < 3 or not os.path.exists(CACHE_PLACAS_FILE):
        return []
    try:
        with open(CACHE_PLACAS_FILE, "r", encoding="utf-8") as f:
            cache_frota = json.load(f)
        placas_filtradas = [p for p in cache_frota if p.replace("-", "").startswith(termo)]
        placas_filtradas.sort()
        return placas_filtradas[:limite]
    except Exception as e:
        print(f"Erro ao ler cache de sugestões: {e}")
        return []

