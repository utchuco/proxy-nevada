import os
import re
import json
import base64
import logging
import requests
import hashlib
from io import BytesIO

# Compatibilidade ReportLab no Python 3.8 (OpenSSL usedforsecurity fix)
try:
    hashlib.md5(usedforsecurity=False)
except TypeError:
    _orig_md5 = hashlib.md5
    def _patched_md5(*args, **kwargs):
        kwargs.pop('usedforsecurity', None)
        return _orig_md5(*args, **kwargs)
    hashlib.md5 = _patched_md5

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfgen import canvas

def extrair_dados_requisicao_pecas(pdf_bytes):
    """
    Utiliza o Gemini para decifrar a folha de Requisição de Peças manuscrita.
    Retorna os dados cadastrais e a lista de itens com quantidades e valores.
    """
    chave_gemini = os.getenv("GEMINI_API_KEY")
    if not chave_gemini:
        logging.warning("GEMINI_API_KEY não configurada no .env")
        return None

    pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

    prompt = """
Você é um assistente especialista em OCR de formulários de 'Requisição de Peças' de oficinas mecânicas preenchidos à mão.
Analise a imagem deste formulário e responda SEMPRE em formato JSON puro, sem blocos markdown.

{
  "oc_num": "Número da O.C se já estiver escrito à mão ou carimbado no campo O.C nº (ex: 121471), caso contrário vazio \"\"",
  "data": "Data escrita no formulário (ex: 22/10/2026)",
  "placa": "Placa do veículo (ex: TJI5E68)",
  "km": "Quilometragem informada (ex: 12.646)",
  "veiculo": "Modelo do veículo informado (ex: HB20 ou POLO)",
  "requisitante": "Nome escrito no campo Requisitante / Cliente (ex: GRADIENTE SOLAR ou CAMARA SBC)",
  "unidade": "Texto do campo Unidade se houver",
  "centro_custo": "Texto do campo Centro de custo se houver",
  "mecanico": "Texto ou nome escrito no campo MEC:",
  "itens": [
    {
      "quantidade": "quantidade numérica informada (ex: 3.5, 1, 0.5)",
      "descricao": "descrição da peça ou material sem o valor unitário se possível (ex: LITROS DE OLEO 5W30)",
      "valor_unitario": "valor numérico unitário da peça marcado com $ (ex: 28.20)"
    }
  ]
}
"""

    modelos = [
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash"
    ]

    for modelo in modelos:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={chave_gemini}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "application/pdf",
                                "data": pdf_b64
                            }
                        }
                    ]
                }
            ]
        }

        try:
            res = requests.post(url, json=payload, timeout=20)
            if res.status_code == 200:
                texto = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                if texto.startswith("```"):
                    linhas = texto.split("\n")
                    if linhas[0].startswith("```"):
                        linhas = linhas[1:]
                    if linhas and linhas[-1].startswith("```"):
                        linhas = linhas[:-1]
                    texto = "\n".join(linhas).strip()
                dados = json.loads(texto)
                return dados
            else:
                logging.warning(f"Tentativa com {modelo} retornou status {res.status_code}: {res.text[:100]}")
        except Exception as e:
            logging.error(f"Erro ao consultar Gemini na requisição de peças ({modelo}): {e}")

    return None

def processar_calculos_pecas(dados_brutos):
    """
    Aplica a regra de cálculo da Nevada:
    - Se a quantidade for != 1 (ex: 3,5 ou 0,5 ou 2), insere ': R$ XX,XX' no final da descrição.
    - Se a quantidade for == 1, mantém a descrição com o valor unitário.
    - Calcula o total geral exato somando os subtotais de todas as linhas.
    """
    itens_brutos = dados_brutos.get("itens") or []
    itens_processados = []
    total_geral = 0.0

    for item in itens_brutos:
        qtd_raw = str(item.get("quantidade", 1)).replace(",", ".").strip()
        try:
            m_qtd = re.search(r"[-+]?\d*\.?\d+", qtd_raw)
            qtd = float(m_qtd.group(0)) if m_qtd else 1.0
        except Exception:
            qtd = 1.0

        vlr_raw = str(item.get("valor_unitario", 0)).replace("R$", "").replace("$", "").replace(" ", "").replace(",", ".").strip()
        try:
            partes = vlr_raw.split(".")
            if len(partes) > 2:
                vlr_raw = "".join(partes[:-1]) + "." + partes[-1]
            m_vlr = re.search(r"[-+]?\d*\.?\d+", vlr_raw)
            unitario = float(m_vlr.group(0)) if m_vlr else 0.0
        except Exception:
            unitario = 0.0

        subtotal = round(qtd * unitario, 2)
        total_geral += subtotal

        # Formatações pt-BR
        subtotal_fmt = f"{subtotal:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        unitario_fmt = f"{unitario:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        if qtd.is_integer():
            qtd_fmt = f"{int(qtd):02d}" if int(qtd) < 10 else str(int(qtd))
        else:
            qtd_fmt = f"{qtd}".replace(".", ",")

        desc_base = str(item.get("descricao", "")).strip().upper()
        # Limpa cálculos e valores antigos da descrição para permitir edição manual limpa
        desc_limpa = re.sub(r"\s*:\s*R\$\s*[\d\.,]+.*$", "", desc_base, flags=re.IGNORECASE).strip()
        desc_pura = re.sub(r"\s*\$\s*[\d\.,]+.*$", "", desc_limpa).strip()

        # Garante a presença do valor unitário na descrição ($ 28,20)
        if unitario > 0:
            desc_com_unit = f"{desc_pura} $ {unitario_fmt}"
        else:
            desc_com_unit = desc_pura

        # Regra 3 do usuário: em todos que for maior ou menor que um, coloca ': R$ total'
        if abs(qtd - 1.0) > 0.001:
            desc_final = f"{desc_com_unit} : R$ {subtotal_fmt}"
        else:
            desc_final = desc_com_unit

        itens_processados.append({
            "quantidade": qtd,
            "quantidade_formatada": qtd_fmt,
            "descricao_pura": desc_pura,
            "descricao": desc_final,
            "valor_unitario": unitario,
            "valor_unitario_formatado": f"R$ {unitario_fmt}",
            "subtotal": subtotal,
            "subtotal_formatado": f"R$ {subtotal_fmt}"
        })

    total_fmt = f"{round(total_geral, 2):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    resultado = dict(dados_brutos)
    resultado["itens"] = itens_processados
    resultado["total_geral"] = round(total_geral, 2)
    resultado["total_geral_formatado"] = f"R$ {total_fmt}"
    return resultado

def gerar_pdf_requisicao_pecas(dados):
    """
    Gera o PDF digital limpo da Requisição de Peças (Opção B) no layout fiel da Nevada.
    Retorna os bytes do PDF gerado.
    """
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    largura, altura = A4

    margem_esq = 36
    margem_dir = largura - 36
    largura_util = margem_dir - margem_esq

    # 1. Cabeçalho
    c.setFont("Helvetica-Bold", 24)
    c.setFillColor(colors.HexColor("#0f172a"))
    c.drawString(margem_esq, altura - 50, "Nevada")

    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(largura / 2, altura - 50, "Requisição de Peças")

    # O.C nº
    c.setFont("Helvetica", 11)
    c.setFillColor(colors.HexColor("#1e293b"))
    c.drawString(margem_dir - 190, altura - 50, "O.C nº")

    c.setStrokeColor(colors.HexColor("#94a3b8"))
    c.setLineWidth(1)
    c.line(margem_dir - 150, altura - 52, margem_dir, altura - 52)

    oc_num = str(dados.get("oc_num") or "").strip()
    if oc_num:
        # Se veio com prefixo 'OC-', pode exibir completo ou apenas o número
        oc_destaque = oc_num if "OC" in oc_num.upper() else f"OC-{oc_num}"
        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(colors.HexColor("#dc2626"))  # Destaque vermelho conforme original
        c.drawString(margem_dir - 145, altura - 48, oc_destaque)

    # Data
    c.setFont("Helvetica", 10)
    c.setFillColor(colors.HexColor("#334155"))
    data_str = str(dados.get("data") or "").strip()
    c.drawString(margem_dir - 150, altura - 70, f"Data: {data_str or '____/____/2026'}")

    # 2. Dados de identificação (bloco esquerdo e direito)
    y_info = altura - 95
    c.setFont("Helvetica-Bold", 9)
    c.drawString(margem_esq, y_info, "Unidade:")
    c.setFont("Helvetica", 9)
    c.drawString(margem_esq + 50, y_info, str(dados.get("unidade") or ""))

    c.setFont("Helvetica-Bold", 9)
    c.drawString(largura / 2 + 30, y_info, "Placa:")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(largura / 2 + 70, y_info, str(dados.get("placa") or "").upper())

    y_info -= 18
    c.setFont("Helvetica-Bold", 9)
    c.drawString(margem_esq, y_info, "Centro de custo:")
    c.setFont("Helvetica", 9)
    c.drawString(margem_esq + 85, y_info, str(dados.get("centro_custo") or ""))

    c.setFont("Helvetica-Bold", 9)
    c.drawString(largura / 2 + 30, y_info, "Km:")
    c.setFont("Helvetica", 9)
    c.drawString(largura / 2 + 70, y_info, str(dados.get("km") or ""))

    # Veículo
    veic = str(dados.get("veiculo") or "").upper()
    if veic:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(margem_dir - 90, y_info, veic)

    y_info -= 18
    c.setFont("Helvetica-Bold", 9)
    c.drawString(margem_esq, y_info, "Requisitante:")
    c.setFont("Helvetica-Bold", 9)
    c.drawString(margem_esq + 70, y_info, str(dados.get("requisitante") or "").upper())

    # 3. Faixa preta "Itens requisitados"
    y_tabela = y_info - 25
    c.setFillColor(colors.HexColor("#0f172a"))
    c.rect(margem_esq, y_tabela, largura_util, 20, fill=1, stroke=0)

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margem_esq + 8, y_tabela + 6, "Itens requisitados")

    c.drawString(margem_dir - 120, y_tabela + 6, f"MEC: {str(dados.get('mecanico') or '').upper()}")

    # 4. Cabeçalho das Colunas
    y_col = y_tabela - 18
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.rect(margem_esq, y_col, largura_util, 18, fill=1, stroke=1)

    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 9)
    largura_qtd = 55
    c.drawCentredString(margem_esq + (largura_qtd / 2), y_col + 5, "Qtdade")
    c.drawString(margem_esq + largura_qtd + 8, y_col + 5, "Descrição")

    # Linha vertical separadora de colunas
    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.line(margem_esq + largura_qtd, y_col, margem_esq + largura_qtd, y_col + 18)

    # 5. Linhas da Tabela (11 linhas padrão do formulário)
    itens = dados.get("itens") or []
    total_linhas = max(11, len(itens))
    altura_linha = 22
    y_atual = y_col

    c.setFont("Helvetica", 9)
    for i in range(total_linhas):
        y_atual -= altura_linha
        # Retângulo da linha
        c.setFillColor(colors.white if i % 2 == 0 else colors.HexColor("#fdfdfd"))
        c.rect(margem_esq, y_atual, largura_util, altura_linha, fill=1, stroke=1)

        # Divisor vertical entre Qtdade e Descrição
        c.line(margem_esq + largura_qtd, y_atual, margem_esq + largura_qtd, y_atual + altura_linha)

        if i < len(itens):
            item = itens[i]
            c.setFillColor(colors.HexColor("#0f172a"))
            c.setFont("Helvetica-Bold", 9)
            c.drawCentredString(margem_esq + (largura_qtd / 2), y_atual + 6, str(item.get("quantidade_formatada", "")))

            c.setFont("Helvetica", 9)
            desc_linha = str(item.get("descricao", ""))
            c.drawString(margem_esq + largura_qtd + 8, y_atual + 6, desc_linha)

    # 6. Linha do TOTAL
    y_total = y_atual - 22
    c.setFillColor(colors.HexColor("#f1f5f9"))
    c.rect(margem_esq, y_total, largura_util, 22, fill=1, stroke=1)
    c.line(margem_esq + largura_qtd, y_total, margem_esq + largura_qtd, y_total + 22)

    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(margem_esq + (largura_qtd / 2), y_total + 6, "TOTAL")

    total_fmt = dados.get("total_geral_formatado") or "R$ 0,00"
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(margem_dir - 12, y_total + 6, total_fmt)

    # 7. Rodapé com as vias e carimbo de estoque
    y_rodape = y_total - 25
    c.setFont("Helvetica", 7.5)
    c.setFillColor(colors.HexColor("#475569"))
    c.drawString(margem_esq, y_rodape, "1 via enviar ao setor de estoque para reposição")
    c.drawString(margem_esq, y_rodape - 12, "2 via guardar como comprovante")

    # Box Entrega no estoque
    c.setStrokeColor(colors.HexColor("#0f172a"))
    c.setFillColor(colors.HexColor("#0f172a"))
    c.rect(margem_dir - 200, y_rodape - 35, 200, 45, fill=0, stroke=1)

    c.rect(margem_dir - 200, y_rodape - 5, 200, 15, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(margem_dir - 100, y_rodape - 1, "Entrega no estoque")

    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica", 8)
    c.drawString(margem_dir - 180, y_rodape - 25, f"Data: {data_str or '____/____/2026'}")

    c.showPage()
    c.save()

    buffer.seek(0)
    return buffer.getvalue()
