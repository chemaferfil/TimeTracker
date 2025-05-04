import os
import sys

# Para que 'from routes...' funcione cuando ejecutes main.py desde la raíz
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, render_template
from flask_socketio import SocketIO                         # ← añadido SocketIO
from models.database import db
from routes.auth import auth_bp
from routes.time import time_bp
from routes.admin import admin_bp
from routes.export import export_bp

app = Flask(
    __name__,
    static_folder='static',
    template_folder='src/templates'
)

# Configuración\ napp.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'asdf#FGSgvasgf$5$WGT')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')

# Inicializar SQLAlchemy
db.init_app(app)
# Inicializar SocketIO
socketio = SocketIO(app, cors_allowed_origins="*")

# Registrar blueprints (cada uno ya trae su propio url_prefix)
app.register_blueprint(auth_bp)
app.register_blueprint(time_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(export_bp)

# Crear tablas si no existen
with app.app_context():
    from models.models import User, TimeRecord  # importa modelos para crear tablas
    db.create_all()

# Ruta de inicio
@app.route('/')
def index():
    return render_template("welcome.html")

if __name__ == '__main__':
    # Usar SocketIO para levantar el servidor con soporte WebSocket
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
