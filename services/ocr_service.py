import os
import json
import base64
import requests
import logging

def extrair_dados_checklist_pdf(pdf_bytes):
    """
    Usa o Gemini via REST API para extrair dados manuscritos do checklist PDF.
    A chave GEMINI_API_KEY vem exclusivamente de os.getenv('GEMINI_API_KEY').
    """
    chave_gemini = os.getenv("GEMINI_API_KEY")
    if not chave_gemini:
        logging.warning("GEMINI_API_KEY não configurada no .env")
        return None

    pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

    prompt = """
Você é um assistente especialista em OCR e extração de checklists de veículos preenchidos à mão.
Analise a imagem deste formulário e responda SEMPRE em formato JSON puro, sem blocos markdown.
{
  "placa": "placa com exatamente 7 caracteres alfanuméricos sem traço (ex: UEX2G82)",
  "data": "DD.MM.AA (ex: 22.10.26)",
  "tipo": "e, s ou es",
  "nome_solicitante": "Nome escrito no campo Nome / Solicitante (do cliente)",
  "telefone_solicitante": "Apenas dígitos numéricos com DDD (ex: 11943245265)",
  "km_atual": "quilometragem numérica informada (apenas números inteiros)",
  "observacoes": "texto manuscrito escrito especificamente no campo 'Observações' ou 'Observação'. NÃO incluir marcações de avarias feitas no desenho da lataria do veículo e NÃO incluir nível de combustível. Apenas o que foi escrito à mão no campo de observações. Se não houver nada escrito, retorne uma string vazia \"\"."
}
"""

    modelos = [
        "gemini-3-flash-preview",
        "gemini-3.6-flash",
        "gemini-3.1-flash-lite-preview",
        "gemini-3.5-flash-lite"
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
            res = requests.post(url, json=payload, timeout=15)
            if res.status_code == 200:
                texto = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                if texto.startswith("```"):
                    linhas = texto.split("\n")
                    if linhas[0].startswith("```"):
                        linhas = linhas[1:]
                    if linhas and linhas[-1].startswith("```"):
                        linhas = linhas[:-1]
                    texto = "\n".join(linhas).strip()
                return json.loads(texto)
            else:
                logging.warning(f"Tentativa com {modelo} retornou status {res.status_code}: {res.text[:100]}")
        except Exception as e:
            logging.error(f"Erro ao consultar Gemini ({modelo}): {e}")

    return None
