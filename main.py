import os
import sys

# Para que 'from src...' funcione cuando ejecutes main.py desde /src
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, render_template
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

# Configuración
app.config['SECRET_KEY'] = 'asdf#FGSgvasgf$5$WGT'

# ——— AÑADE ESTE BLOQUE justo aquí ———
if os.getenv('DATABASE_URL'):
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
else:
    # Usa SQLite local, crea timetracker.db junto a main.py
    basedir = os.path.abspath(os.path.dirname(__file__))
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'timetracker.db')
# ————————————————————————————————

# desactivar warnings innecesarios
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Inicializar SQLAlchemy
db.init_app(app)

# Registrar blueprints (cada uno ya trae su propio url_prefix)
app.register_blueprint(auth_bp)
app.register_blueprint(time_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(export_bp)

# Crear tablas si no existen
with app.app_context():
    from models.models import User, TimeRecord
    db.create_all()

# Ruta de inicio
@app.route('/')
def index():
    return render_template("welcome.html")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
