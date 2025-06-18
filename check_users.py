from main import app, db
from models.models import User

def main():
    with app.app_context():
        users = db.session.query(User).all()
        if not users:
            print("🔍 No hay usuarios registrados.")
        else:
            print("📋 Usuarios existentes:")
            for u in users:
                print(f" - ID {u.id}: {u.username} (admin={getattr(u, 'is_admin', False)})")

if __name__ == "__main__":
    main()
