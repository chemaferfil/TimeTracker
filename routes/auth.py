from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from werkzeug.security import check_password_hash, generate_password_hash
from models.models import User, TimeRecord
from models.database import db
from datetime import date, datetime, timedelta  # Import timedelta
from sqlalchemy import desc # Import desc for ordering

auth_bp = Blueprint("auth", __name__, template_folder="../templates")

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            session["user_id"] = user.id
            session["is_admin"] = user.is_admin
            flash("Inicio de sesión exitoso.", "success")
            if user.is_admin:
                return redirect(url_for("admin.dashboard"))  # Corrected redirect for admin
            else:
                return redirect(url_for("auth.dashboard"))
        else:
            flash("Nombre de usuario o contraseña incorrectos.", "danger")
            
    return render_template("login.html")

@auth_bp.route("/logout")
def logout():
    session.pop("user_id", None)
    session.pop("is_admin", None)
    flash("Has cerrado sesión.", "info")
    return redirect(url_for("auth.login"))

@auth_bp.route("/registro", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        full_name = request.form.get("full_name") # Get full name
        email = request.form.get("email")         # Get email
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if password != confirm_password:
            flash("Las contraseñas no coinciden.", "danger")
            return redirect(url_for("auth.register"))

        # Check if username or email already exists
        if User.query.filter_by(username=username).first():
            flash("El nombre de usuario ya existe.", "danger")
            return redirect(url_for("auth.register"))
        if User.query.filter_by(email=email).first():
            flash("El email ya está registrado.", "danger")
            return redirect(url_for("auth.register"))

        # Create user object with required fields (excluding password initially)
        nuevo_usuario = User(username=username, full_name=full_name, email=email, is_admin=False)
        # Set password using the dedicated method which handles hashing
        nuevo_usuario.set_password(password)

        db.session.add(nuevo_usuario)
        db.session.commit()

        flash("Registro exitoso. Ya puedes iniciar sesión.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html")

@auth_bp.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    user_id = session["user_id"]
    user = User.query.get(user_id)
    if not user:
        flash("Usuario no encontrado.", "danger")
        return redirect(url_for("auth.logout")) # Log out if user not found

    today = date.today()
    # Find the current open record for the user, if any (same logic as in time.py)
    today_record = (
        TimeRecord.query
            .filter_by(user_id=user_id, check_out=None)
            .order_by(desc(TimeRecord.id))
            .first()
    )

    # Fetch recent records (e.g., last 5)
    recent_records = TimeRecord.query.filter_by(user_id=user_id).order_by(desc(TimeRecord.date), desc(TimeRecord.check_in)).limit(5).all()

    # Helper function to format timedelta (same as in time.py)
    def format_timedelta(td):
        if td is None:
            return "-"
        total_seconds = int(td.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    # Calculate worked time for recent records
    recent_records_with_duration = []
    for record in recent_records:
        duration = None
        if record.check_in and record.check_out:
            duration = record.check_out - record.check_in
        recent_records_with_duration.append({
            "record": record,
            "duration_formatted": format_timedelta(duration)
        })

    return render_template("employee_dashboard.html", 
                           user=user, 
                           today_record=today_record, 
                           recent_records=recent_records_with_duration, # Pass records with duration
                           current_date=today)

# Remove the old admin_dashboard function from auth.py if it exists
# The correct admin dashboard route is now in admin.py


