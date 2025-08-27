import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from flask import Flask
from models.database import db
from models.models import User

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(os.path.dirname(__file__), '..', 'timetracker.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

from werkzeug.security import check_password_hash

db.init_app(app)

if __name__ == '__main__':
    with app.app_context():
        users = User.query.order_by(User.username).all()
        print(f"Total users: {len(users)}")
        for u in users:
            print(f"- {u.username} | admin={u.is_admin} | centro={u.centro} | email={u.email}")
        print("\nCheck pass '2025' for target admins:")
        for name in ['Mercedes','Valentina','Juan','Lorena']:
            u = User.query.filter_by(username=name).first()
            if not u:
                print(f"{name}: NOT FOUND")
            else:
                print(f"{name}: found, password_ok={check_password_hash(u.password_hash, '2025')}")

