import os
from flask import Flask
from dotenv import load_dotenv

from extensions import limiter
from routes.motorista import motorista_bp
from routes.painel import painel_bp
from routes.api import api_bp
from routes.oficina import oficina_bp

load_dotenv()

app = Flask(__name__)

# Configurações e Inicialização de Extensões
limiter.init_app(app)

# Registro de Blueprints
app.register_blueprint(motorista_bp)
app.register_blueprint(painel_bp)
app.register_blueprint(api_bp)
app.register_blueprint(oficina_bp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)