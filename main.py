import eventlet
eventlet.monkey_patch()
import os
import sys

# Para que 'from src...' funcione cuando ejecutes main.py desde /src
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, render_template
from models.database import db
from flask_migrate import Migrate, upgrade as migrate_upgrade
from routes.auth import auth_bp
from routes.time import time_bp
from routes.admin import admin_bp
from routes.export import export_bp
from sqlalchemy.pool import NullPool

# Crear instancia de la app Flask
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

# Configure SQLAlchemy engine options based on environment
is_production = os.getenv('DYNO') or os.getenv('RENDER')
if is_production:
    # Production environment with eventlet - use NullPool
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        # NullPool doesn't support pool_timeout or max_overflow
        "poolclass": NullPool
    }
else:
    # Development environment - use default pooling
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_timeout": 20,
        "max_overflow": 0
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

# Ruta de inicio
@app.route('/')
def index():
    return render_template("welcome.html")

def init_db():
    """Initialize database tables and run migrations"""
    with app.app_context():
        from models.models import User, TimeRecord
        migrate_upgrade()
        db.create_all()

if __name__ == '__main__':
    # Solo inicializar la base de datos cuando se ejecuta directamente (no con gunicorn)
    init_db()
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
else:
    # Cuando se ejecuta con gunicorn, inicializar la base de datos después de crear la app
    init_db()
