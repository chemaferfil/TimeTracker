from main import app, db
from models.models import User
from werkzeug.security import generate_password_hash

def main():
    with app.app_context():
        normal = User(
            username='Sergio',
            password_hash=generate_password_hash('123'),
            full_name='Sergio Local',
            email='sergio@local',
            is_admin=False,
            is_active=True
        )
        admin = User(
            username='Admin',
            password_hash=generate_password_hash('123'),
            full_name='Administrador',
            email='admin@local',
            is_admin=True,
            is_active=True
        )
        db.session.add_all([normal, admin])
        db.session.commit()
        print("✅ Usuarios creados correctamente")

if __name__ == "__main__":
    main()
