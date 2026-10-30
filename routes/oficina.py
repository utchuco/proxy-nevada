from flask import Blueprint, render_template, request, jsonify
from services.bluefleet_service import consultar_historico_pecas_veiculo

oficina_bp = Blueprint("oficina", __name__)

@oficina_bp.route("/oficina", methods=["GET", "POST"])
def pagina_oficina():
    placa = ""
    if request.method == "POST":
        placa = request.form.get("placa", "").strip().upper()
    else:
        placa = request.args.get("placa", "").strip().upper()

    if not placa:
        return render_template("oficina.html", consultado=False)

    dados_veiculo, pecas, erro = consultar_historico_pecas_veiculo(placa)

    return render_template(
        "oficina.html",
        consultado=True,
        placa=placa,
        veiculo=dados_veiculo,
        pecas=pecas,
        erro=erro
    )

@oficina_bp.route("/oficina/api/consultar/<placa>", methods=["GET"])
def api_consultar_oficina(placa):
    dados_veiculo, pecas, erro = consultar_historico_pecas_veiculo(placa)
    if erro:
        return jsonify({"isSuccess": False, "erro": erro}), 400
    return jsonify({
        "isSuccess": True,
        "veiculo": dados_veiculo,
        "pecas": pecas,
        "total": len(pecas)
    }), 200

