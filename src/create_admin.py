import sys
import os

# Añadir la carpeta raíz del proyecto al path para permitir imports relativos
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from models.models import User
from models.database import db
from flask import Flask

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = "mysql+pymysql://root:Fichaje2025!@localhost:3306/fichajes"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'temporal_secret'

# Inicializar SQLAlchemy ANTES del contexto
db.init_app(app)

with app.app_context():
    db.create_all()

    # Verificar si ya existe el usuario admin
    if not User.query.filter_by(username='admin').first():
        user = User(
            username='admin',
            full_name='Administrador',
            email='admin@example.com',
            is_admin=True,
            is_active=True
        )
        user.set_password('admin123')
        db.session.add(user)
        db.session.commit()
        print("✅ Usuario admin creado con éxito.")
    else:
        print("ℹ️ Ya existe un usuario con el nombre 'admin'.")
