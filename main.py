import os
import sys

# Para que 'from src...' funcione cuando ejecutes main.py desde /src
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, render_template
from models.database import db
from flask_migrate import Migrate
from routes.auth import auth_bp
from routes.time import time_bp
from routes.admin import admin_bp
from routes.export import export_bp

app = Flask(
    __name__,
    static_folder='static',
    template_folder='src/templates'
)

# Configuración general
app.config['SECRET_KEY'] = 'asdf#FGSgvasgf$5$WGT'

# Configuración de la base de datos
uri = os.getenv("DATABASE_URL")

if not uri:
    print("DATABASE_URL no está definido — usando SQLite local.", file=sys.stderr)
    basedir = os.path.abspath(os.path.dirname(__file__))
    uri = 'sqlite:///' + os.path.join(basedir, 'timetracker.db')
else:
    uri = uri.replace("postgres://", "postgresql://")  # Compatibilidad con Render

app.config['SQLALCHEMY_DATABASE_URI'] = uri
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True
}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Inicializar extensiones
db.init_app(app)
migrate = Migrate(app, db)

# Registrar blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(time_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(export_bp)

# Crear tablas si no existen (útil para desarrollo rápido)
with app.app_context():
    from models.models import User, TimeRecord
    db.create_all()

# Ruta de inicio
@app.route('/')
def index():
    return render_template("welcome.html")

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
