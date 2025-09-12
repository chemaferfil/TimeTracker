# wsgi.py - Entry point for production deployment with eventlet
import eventlet
# Patch all modules before importing anything else
eventlet.monkey_patch(thread=True, socket=True, select=True, time=True, os=True)

import os
import sys

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import app

# Inicializar la base de datos en el contexto de aplicación
def initialize_app():
    """Initialize database for production"""
    with app.app_context():
        try:
            from models.models import User, TimeRecord
            from flask_migrate import upgrade as migrate_upgrade
            from models.database import db
            
            # Run migrations
            migrate_upgrade()
            # Ensure tables are created
            db.create_all()
            print("Database initialized successfully", flush=True)
        except Exception as e:
            print(f"Warning: Error initializing database: {e}", flush=True)
            # Don't fail the deployment if database init fails
            pass

# Initialize database when imported (for gunicorn)
try:
    initialize_app()
except Exception as e:
    print(f"Warning: Could not initialize app: {e}", flush=True)

if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
