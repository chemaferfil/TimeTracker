from models.models import User

print("🔍 Campos mapeados del modelo User:")
print(User.__table__.columns.keys())
