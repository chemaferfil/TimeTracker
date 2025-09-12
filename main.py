import os
import sys

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template
from models.database import db
from flask_migrate import Migrate, upgrade as migrate_upgrade
from routes.auth import auth_bp
from routes.time import time_bp
from routes.admin import admin_bp
from routes.export import export_bp
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    import atexit
    SCHEDULER_AVAILABLE = True
except ImportError as e:
    print(f"APScheduler not available: {e}", file=sys.stderr)
    SCHEDULER_AVAILABLE = False

# Crear instancia de la app Flask
app = Flask(
    __name__,
    static_folder='static',
    template_folder='src/templates'
)

# Configuración general
app.config['SECRET_KEY'] = 'asdf#FGSgvasgf$5$WGT'

# Configuración de la base de datos
# Forzamos el uso de la BD de Render para las pruebas locales (según tu petición)
render_dsn = (
    "postgresql://timetracker_db_ntuk_user:"
    "iRlZxk7xdpA38AMYOIOZMt2lsyL1ST8l@"
    "dpg-d2h0c78dl3ps73fq6s80-a.oregon-postgres.render.com:5432/"
    "timetracker_db_ntuk?sslmode=require"
)
uri = os.getenv("RENDER_DATABASE_URL") or os.getenv("DATABASE_URL")
# Si la URI no existe o apunta a MySQL, forzamos la de Render
if not uri or uri.lower().startswith("mysql"):
    uri = render_dsn
# Normalizar si viniera como postgres://
uri = uri.replace("postgres://", "postgresql://")
app.config['SQLALCHEMY_DATABASE_URI'] = uri
print("Usando BD:", app.config['SQLALCHEMY_DATABASE_URI'], file=sys.stderr)

# Configure SQLAlchemy engine options based on environment
is_production = os.getenv('DYNO') or os.getenv('RENDER')
if is_production:
    # Production environment - standard pooling for sync workers
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_size": 10,
        "max_overflow": 20,
        "pool_timeout": 30
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
# Log rápido del driver efectivo
try:
    with app.app_context():
        print("Driver:", db.engine.url.drivername, file=sys.stderr)
except Exception:
    pass
# Log de diagnóstico por request para confirmar motor/URL
@app.before_request
def _log_db_on_request():
    try:
        from flask import request
        print(f"[REQ] {request.method} {request.path} -> engine={db.engine.url.drivername} url={db.engine.url}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[REQ] engine-info error: {e}", file=sys.stderr, flush=True)

migrate = Migrate(app, db)

# Proper session cleanup
@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()

# Context processor para hacer disponible el usuario actual y saludo
@app.context_processor
def inject_user():
    from flask import session
    from models.models import User
    from datetime import datetime

    user = None
    greeting = ""

    user_id = session.get("user_id")
    if user_id:
        user = User.query.get(user_id)
        if user:
            # Obtener solo el primer nombre
            first_name = user.full_name.split()[0] if user.full_name else user.username

            # Determinar saludo según la hora
            hour = datetime.now().hour
            if 6 <= hour < 12:
                greeting = f"Buenos días, {first_name}"
            elif 12 <= hour < 20:
                greeting = f"Buenas tardes, {first_name}"
            else:
                greeting = f"Buenas noches, {first_name}"

    return dict(current_user=user, greeting=greeting)

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
        # Si estamos en SQLite local, evita correr migraciones de Alembic
        try:
            driver = db.engine.url.drivername
        except Exception:
            driver = None

        if driver and driver.startswith("sqlite"):
            db.create_all()
            return

        # Para motores no-SQLite (p.ej., Postgres con datos reales), no tocar el esquema
        # para evitar problemas de dependencias o drivers en local. Asumimos que la BD ya
        # está provisionada (como la de Render descargada).
        return

def init_scheduler():
    """Initialize the background scheduler for automatic tasks"""
    if not SCHEDULER_AVAILABLE:
        app.logger.warning("APScheduler not available - automatic closing disabled")
        return
        
    try:
        scheduler = BackgroundScheduler(daemon=True)
        
        # Import the task function
        from tasks.scheduler import auto_close_open_records
        
        # Schedule the auto-close task to run daily at 23:59:59
        scheduler.add_job(
            func=auto_close_open_records,
            trigger=CronTrigger(hour=23, minute=59, second=59),
            id='auto_close_records',
            name='Auto-close open time records',
            replace_existing=True
        )
        
        scheduler.start()
        app.logger.info("Scheduler initialized - Auto-close task scheduled for 23:59:59 daily")
        
        # Shut down the scheduler when exiting the app
        atexit.register(lambda: scheduler.shutdown())
    except Exception as e:
        app.logger.error(f"Failed to initialize scheduler: {e}")
        app.logger.warning("Automatic closing disabled due to scheduler error")

if __name__ == '__main__':
    # Solo inicializar la base de datos cuando se ejecuta directamente (no con gunicorn)
    init_db()
    init_scheduler()
    port = int(os.getenv('PORT', 5000))
    # En producción usar debug=False
    debug_mode = not (os.getenv('DYNO') or os.getenv('RENDER'))
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
else:
    # Cuando se ejecuta con gunicorn, inicializar la base de datos después de crear la app
    init_db()
    init_scheduler()
