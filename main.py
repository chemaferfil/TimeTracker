import os
from flask import Flask, render_template
from flask_socketio import SocketIO
from models.database import db
from routes.auth import auth_bp
from routes.time import time_bp
from routes.admin import admin_bp
from routes.export import export_bp

app = Flask(
    __name__,
    static_folder='static',
    template_folder='templates'
)

# Configuración
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'asdf#FGSgvasgf$5$WGT')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')

# Inicializar extensiones
db.init_app(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Registrar blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(time_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(export_bp)

# Crear tablas si no existen
def create_tables():
    from models.models import User, TimeRecord
    db.create_all()

with app.app_context():
    create_tables()

# Ruta de inicio
@app.route('/')
def index():
    return render_template('welcome.html')

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=True)
