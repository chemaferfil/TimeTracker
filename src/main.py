import os
import sys

# Para que 'from src...' funcione cuando ejecutes main.py desde /src
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, render_template
from src.models.database import db
from src.routes.auth import auth_bp
from src.routes.time import time_bp
from src.routes.admin import admin_bp
from src.routes.export import export_bp

app = Flask(
    __name__,
    static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static'),
    template_folder=os.path.join(os.path.dirname(__file__), 'templates')
)

# Configuración
app.config['SECRET_KEY'] = 'asdf#FGSgvasgf$5$WGT'
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')

# Inicializar SQLAlchemy
db.init_app(app)

# Registrar blueprints (cada uno ya trae su propio url_prefix)
app.register_blueprint(auth_bp)
app.register_blueprint(time_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(export_bp)

# Crear tablas si no existen
with app.app_context():
    from src.models.models import User, TimeRecord
    db.create_all()

# Ruta de inicio
@app.route('/')
def index():
    return render_template("welcome.html")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
