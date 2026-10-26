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

def desenhar_requisicao_layout(c, dados, x_offset=36, y_top=805):
    """
    Desenha uma cópia exata da Requisição de Peças (fiel ao modelo Excel / Nevada).
    """
    largura_tabela = 523
    
    # Cores fiéis ao Excel
    cor_azul_nevada = colors.HexColor("#002060")
    cor_cinza_escuro = colors.HexColor("#1f2937")  # Cabeçalhos Itens requisitados e Entrega
    cor_cinza_claro = colors.HexColor("#e5e7eb")   # Fundo quadrado cinza e cabeçalho colunas
    cor_texto_escuro = colors.HexColor("#111827")
    cor_borda = colors.HexColor("#9ca3af")
    cor_borda_tabela = colors.HexColor("#4b5563")

    # 1. Cabeçalho
    # "Nevada" (Calibri/Helvetica Bold 34pt Azul Marinho)
    c.setFont("Helvetica-Bold", 34)
    c.setFillColor(cor_azul_nevada)
    c.drawString(x_offset, y_top - 32, "Nevada")

    # "Requisição de Peças" (Helvetica 17pt)
    c.setFont("Helvetica", 17)
    c.setFillColor(cor_texto_escuro)
    c.drawString(x_offset + 175, y_top - 18, "Requisição de Peças")

    # O.C nº
    c.setFont("Helvetica", 11)
    c.setFillColor(cor_texto_escuro)
    c.drawString(x_offset + 320, y_top - 32, "O.C nº")
    # Linha para a O.C
    c.setStrokeColor(colors.HexColor("#64748b"))
    c.setLineWidth(1)
    c.line(x_offset + 355, y_top - 33, x_offset + largura_tabela, y_top - 33)

    # Número da O.C em destaque vermelho caso exista
    oc_num = str(dados.get("oc_num") or "").strip()
    if oc_num:
        oc_destaque = oc_num.replace("OC-", "").replace("OC", "").strip()
        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(colors.HexColor("#dc2626"))  # Vermelho conforme caneta/sistema
        c.drawString(x_offset + 365, y_top - 30, oc_destaque)

    # Data no cabeçalho
    data_str = str(dados.get("data") or "").strip()
    if not data_str:
        data_str = "      /          /2026"
    c.setFont("Helvetica", 10)
    c.setFillColor(cor_texto_escuro)
    c.drawString(x_offset + 360, y_top - 50, "Data")
    c.drawString(x_offset + 395, y_top - 50, data_str)

    # 2. Seção de Identificação: Unidade, Centro de Custo, Requisitante & Placa, Km, Veículo
    y_id = y_top - 70
    c.setFont("Helvetica", 10)
    c.drawString(x_offset, y_id, "Unidade")
    c.drawString(x_offset, y_id - 16, "Centro de custo")
    c.drawString(x_offset, y_id - 32, "Requisitante")

    # Quadrado cinza no canto superior esquerdo (Unidade até Requisitante)
    c.setFillColor(cor_cinza_claro)
    c.setStrokeColor(cor_borda)
    c.setLineWidth(0.5)
    c.rect(x_offset + 80, y_id - 36, 185, 48, fill=1, stroke=1)

    # Linhas divisórias internas do bloco cinza
    c.line(x_offset + 80, y_id - 4, x_offset + 265, y_id - 4)
    c.line(x_offset + 80, y_id - 20, x_offset + 265, y_id - 20)

    # Preenchimento dos dados dentro do bloco cinza
    c.setFillColor(cor_texto_escuro)
    c.setFont("Helvetica", 9)
    if dados.get("unidade"):
        c.drawString(x_offset + 85, y_id + 1, str(dados.get("unidade")))
    if dados.get("centro_custo"):
        c.drawString(x_offset + 85, y_id - 15, str(dados.get("centro_custo")))

    # NOME DO CLIENTE NO QUADRADO CINZA (Campo Requisitante) - DESTAQUE OFICIAL
    requisitante = str(dados.get("cliente_nome") or dados.get("requisitante") or "").strip().upper()
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x_offset + 85, y_id - 31, requisitante)

    # Lado direito: Placa, Km, Veículo
    c.setFont("Helvetica", 10)
    c.drawString(x_offset + 285, y_id - 16, "placa")
    placa = str(dados.get("placa") or "").strip().upper()
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x_offset + 325, y_id - 16, placa)

    c.setFont("Helvetica", 10)
    c.drawString(x_offset + 285, y_id - 32, "Km")
    km = str(dados.get("km") or "").strip()
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x_offset + 325, y_id - 32, km)

    veiculo = str(dados.get("veiculo") or "").strip().upper()
    if veiculo:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x_offset + 420, y_id - 32, veiculo)

    # 3. Tabela de Peças
    y_tab_top = y_id - 48
    largura_qtd = 50
    largura_mec = 45
    largura_desc = largura_tabela - largura_qtd - largura_mec

    # Faixa "Itens requisitados" (Escura)
    c.setFillColor(cor_cinza_escuro)
    c.rect(x_offset, y_tab_top - 18, largura_tabela - largura_mec, 18, fill=1, stroke=0)

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x_offset + 8, y_tab_top - 13, "Itens requisitados")

    # Bloco vertical MEC: à direita da tabela
    altura_tabela_corpo = 18 + 18 + (9 * 16) + 18  # título + colunas + 9 linhas + total
    c.setFillColor(colors.white)
    c.setStrokeColor(cor_borda_tabela)
    c.setLineWidth(0.8)
    c.rect(x_offset + largura_tabela - largura_mec, y_tab_top - altura_tabela_corpo, largura_mec, altura_tabela_corpo, fill=0, stroke=1)

    c.setFillColor(cor_texto_escuro)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x_offset + largura_tabela - largura_mec + 8, y_tab_top - 14, "MEC:")
    mec_nome = str(dados.get("mecanico") or "").strip().upper()
    if mec_nome:
        c.setFont("Helvetica-Bold", 8)
        palavras = mec_nome.split()
        for idx_m, p in enumerate(palavras[:2]):
            c.drawString(x_offset + largura_tabela - largura_mec + 4, y_tab_top - 30 - (idx_m * 12), p)

    # Cabeçalho das Colunas: Qtdade | Descrição
    y_col = y_tab_top - 36
    c.setFillColor(cor_cinza_claro)
    c.setStrokeColor(cor_borda_tabela)
    c.setLineWidth(0.8)
    c.rect(x_offset, y_col, largura_qtd, 18, fill=1, stroke=1)
    c.rect(x_offset + largura_qtd, y_col, largura_desc, 18, fill=1, stroke=1)

    c.setFillColor(cor_texto_escuro)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(x_offset + (largura_qtd / 2), y_col + 5, "Qtdade")
    c.drawString(x_offset + largura_qtd + 10, y_col + 5, "Descrição")

    # Linhas de Itens (9 linhas de dados)
    itens = dados.get("itens") or []
    y_row = y_col
    altura_linha = 16
    for i in range(9):
        y_row -= altura_linha
        c.setFillColor(colors.white)
        c.setStrokeColor(cor_borda_tabela)
        c.setLineWidth(0.5)
        c.rect(x_offset, y_row, largura_qtd, altura_linha, fill=1, stroke=1)
        c.rect(x_offset + largura_qtd, y_row, largura_desc, altura_linha, fill=1, stroke=1)

        if i < len(itens):
            item = itens[i]
            c.setFillColor(cor_texto_escuro)
            c.setFont("Helvetica", 9)
            
            # Quantidade
            qtd_txt = str(item.get("quantidade_formatada") or item.get("quantidade") or "")
            c.drawCentredString(x_offset + (largura_qtd / 2), y_row + 4, qtd_txt)

            # Descrição completa com R$
            desc_txt = str(item.get("descricao") or "").strip()
            c.drawString(x_offset + largura_qtd + 8, y_row + 4, desc_txt)

    # Linha do TOTAL
    y_total = y_row - 18
    c.setFillColor(colors.white)
    c.rect(x_offset, y_total, largura_qtd, 18, fill=1, stroke=1)
    
    largura_total_label = largura_desc - 90
    c.rect(x_offset + largura_qtd, y_total, largura_total_label, 18, fill=1, stroke=1)
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(cor_texto_escuro)
    c.drawRightString(x_offset + largura_qtd + largura_total_label - 10, y_total + 5, "TOTAL")

    # Célula com valor do Total Geral
    largura_total_val = 90
    c.setFillColor(colors.white)
    c.rect(x_offset + largura_qtd + largura_total_label, y_total, largura_total_val, 18, fill=1, stroke=1)
    c.setFillColor(cor_texto_escuro)
    c.setFont("Helvetica-Bold", 10)
    total_val_str = str(dados.get("total_geral_formatado") or "").strip()
    if total_val_str:
        c.drawRightString(x_offset + largura_tabela - largura_mec - 8, y_total + 5, total_val_str)

    # 4. Rodapé
    y_rodape = y_total - 10
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#4b5563"))
    c.drawString(x_offset, y_rodape - 8, "1 via enviar ao setor de estoque para reposição")
    c.drawString(x_offset, y_rodape - 20, "2 via guardar como comprovante")

    # Lado direito: Bloco "Entrega no estoque"
    largura_entrega = 185
    x_entrega = x_offset + largura_tabela - largura_entrega

    # Faixa superior escura "Entrega no estoque"
    c.setFillColor(cor_cinza_escuro)
    c.rect(x_entrega, y_rodape - 14, largura_entrega, 14, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(x_entrega + 8, y_rodape - 10, "Entrega no estoque")

    # Caixa de assinatura e data
    c.setFillColor(colors.white)
    c.setStrokeColor(cor_borda_tabela)
    c.rect(x_entrega, y_rodape - 42, largura_entrega, 28, fill=0, stroke=1)

    c.setFillColor(cor_texto_escuro)
    c.setFont("Helvetica", 9)
    c.drawString(x_entrega + 75, y_rodape - 38, "Data")
    c.drawString(x_entrega + 100, y_rodape - 38, data_str)

def gerar_pdf_requisicao_pecas(dados):
    """
    Gera o PDF digital limpo da Requisição de Peças (Opção B) no layout 100% fiel à Nevada / Excel.
    Recortado para conter apenas 1 via única (meia folha / 595.27 x 421.0 pt), sem espaços sobrando.
    Retorna os bytes do PDF gerado.
    """
    buffer = BytesIO()
    tamanho_meia_folha = (595.27, 421.0)
    c = canvas.Canvas(buffer, pagesize=tamanho_meia_folha)
    
    # Desenha apenas 1 via recortada no tamanho exato
    desenhar_requisicao_layout(c, dados, x_offset=36, y_top=405)
    
    c.showPage()
    c.save()

    buffer.seek(0)
    return buffer.getvalue()
