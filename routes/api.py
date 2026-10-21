import uuid
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify
from services.storage_service import (
    salvar_novo_checklist_pendente,
    salvar_pdf_checklist,
    buscar_sugestoes_placas
)
from services.bluefleet_service import enriquecer_dados_veiculo

api_bp = Blueprint("api", __name__, url_prefix="/api")

@api_bp.route("/receber-checklist", methods=["POST"])
def receber_checklist():
    dados = request.get_json()

    if not dados or not (dados.get("placa") or dados.get("placa_veiculo")):
        return jsonify({"isSuccess": False, "erro": "Dados ou placa ausentes."}), 400

    placa_bruta = str(dados.get("placa") or dados.get("placa_veiculo")).upper().strip()
    placa_limpa = placa_bruta.replace("-", "").replace(" ", "")

    # Define data futura sugerida padrão (amanhã às 10:00) se não informada
    amanha_iso = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT10:00:00")
    data_agendamento = dados.get("data_agendamento") or dados.get("data_sugerida") or amanha_iso

    # Tratamento do tipo de ocorrência
    tipo_input = str(dados.get("tipo_manutencao") or "").lower()
    motivo_id = dados.get("motivo_id")
    motivo_classe = dados.get("motivo_classe")

    if not motivo_id:
        if "prev" in tipo_input or "revis" in tipo_input:
            motivo_id = "PreventiveMaintenance"
            motivo_classe = motivo_classe or "PreventiveMaintenanceQuilometerRevision"
        else:
            motivo_id = "CorrectiveMaintenance"
            motivo_classe = motivo_classe or "CorrectiveMaintenanceMechanicalFailure"

    id_aprovacao = str(uuid.uuid4())

    # Processamento do PDF do Checklist (Base64)
    caminho_pdf_salvo = None
    pdf_base64 = dados.get("pdf_base64")
    pdf_nome = dados.get("pdf_nome") or f"Checklist_{placa_limpa}.pdf"

    if pdf_base64:
        caminho_pdf_salvo = salvar_pdf_checklist(placa_limpa, id_aprovacao, pdf_base64)

    # Enriquecimento automático com dados da Blue Fleet (Modelo, Cliente, Contrato)
    dados_veiculo = enriquecer_dados_veiculo(placa_limpa)

    novo_pendente = {
        "id_aprovacao": id_aprovacao,
        "placa": placa_limpa,
        "km_atual": dados.get("km_atual") or dados.get("km") or 0,
        "tipo_veiculo": dados.get("tipo_veiculo", "alugado").lower(),
        "modelo_veiculo": dados_veiculo.get("modelo_veiculo", ""),
        "montadora": dados_veiculo.get("montadora", ""),
        "cliente_nome": dados_veiculo.get("cliente_nome", ""),
        "contrato_id": dados_veiculo.get("contrato_id"),
        "contract_item_id": dados_veiculo.get("contract_item_id"),
        "motivo_id": str(motivo_id),
        "motivo_classe": str(motivo_classe),
        "plano_revisao_id": str(dados.get("plano_revisao_id", "149")),
        "descricao": dados.get("descricao") or dados.get("defeito_relatado") or "Manutenção",
        "data_agendamento": data_agendamento,
        "solicitante": dados.get("solicitante") or dados.get("motorista_nome") or "Equipe Nevada",
        "telefone": dados.get("telefone") or dados.get("motorista_telefone") or "11999999999",
        "email": dados.get("email") or dados.get("motorista_email") or "frota@locadoranevada.com.br",
        "leva_e_traz": bool(dados.get("leva_e_traz", False)),
        "caminho_pdf": caminho_pdf_salvo,
        "pdf_nome": pdf_nome,
        "data_recebimento": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "status": "Aguardando Aprovação"
    }

    salvar_novo_checklist_pendente(novo_pendente)

    return jsonify({
        "isSuccess": True,
        "mensagem": "Checklist completo recebido e guardado para aprovação!",
        "id_aprovacao": novo_pendente["id_aprovacao"]
    }), 200

@api_bp.route("/veiculos/sugestoes", methods=["GET"])
def api_sugestoes():
    termo = request.args.get("q", "")
    placas = buscar_sugestoes_placas(termo)
    return jsonify({"placas": placas})
