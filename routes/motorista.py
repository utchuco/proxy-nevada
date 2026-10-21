import os
from flask import Blueprint, render_template, request, send_file, Response
from extensions import limiter
from services.bluefleet_service import (
    buscar_dados_veiculo_e_reserva,
    buscar_checklist_pdf_bluefleet
)
from services.storage_service import buscar_crlv_path

motorista_bp = Blueprint("motorista", __name__)

@motorista_bp.route("/")
def index():
    return render_template("index.html")

@motorista_bp.route("/buscar", methods=["POST"])
def buscar():
    placa = request.form.get("placa", "").strip().upper()
    if not placa:
        return render_template("index.html", erro="Por favor, digite uma placa válida.")

    carro_titular, carro_reserva, aviso_reserva, erro = buscar_dados_veiculo_e_reserva(placa)
    if erro:
        return render_template("index.html", erro=erro)

    return render_template("resultado.html", titular=carro_titular, reserva=carro_reserva, aviso_reserva=aviso_reserva)

@motorista_bp.route("/checklist/<placa>")
def buscar_checklist(placa):
    pdf_conteudo, nome_arquivo, erro = buscar_checklist_pdf_bluefleet(placa)
    if erro:
        status_code = 404 if "não encontrado" in erro.lower() else 500
        return erro, status_code

    return Response(
        pdf_conteudo,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"inline; filename={nome_arquivo}"}
    )

@motorista_bp.route("/crlv", methods=["POST"])
@limiter.limit("5 per minute")
def acessar_crlv():
    placa = request.form.get("placa")
    senha = request.form.get("senha")

    senha_segura = os.getenv("PIN_CRLV", "BloqueioEmergenciaNevada2026")

    if senha != senha_segura:
        return "Acesso negado: Senha incorreta ou limite de tentativas excedido.", 403

    caminho_arquivo = buscar_crlv_path(placa)
    if caminho_arquivo:
        return send_file(caminho_arquivo, mimetype="application/pdf")

    return f"Documento não encontrado para a placa {placa}.", 404
