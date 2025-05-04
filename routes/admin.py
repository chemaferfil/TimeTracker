from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from functools import wraps
from models.models import User, TimeRecord
from models.database import db
from werkzeug.security import generate_password_hash
from datetime import datetime, timedelta

admin_bp = Blueprint("admin", __name__, template_folder="../templates", url_prefix="/admin")

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Acceso no autorizado. Se requieren permisos de administrador.", "danger")
            return redirect(url_for("auth.login"))
        user = User.query.get(session.get("user_id"))
        if not user or not user.is_admin:
            session.clear()
            flash("Tu cuenta ya no tiene permisos de administrador.", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function

@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    total_users = User.query.filter_by(is_admin=False).count()
    active_users = (
        db.session
          .query(TimeRecord.user_id)
          .filter(
              TimeRecord.check_in.isnot(None),
              TimeRecord.check_out.is_(None)
          )
          .distinct()
          .count()
    )
    recent_records = (
        TimeRecord.query
        .join(User, TimeRecord.user_id == User.id)
        .order_by(TimeRecord.id.desc())
        .limit(10)
        .all()
    )
    def format_timedelta(td):
        if td is None:
            return "-"
        total_seconds = int(td.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"
    recent_records_with_duration = []
    for record in recent_records:
        duration = None
        if record.check_in and record.check_out:
            duration = record.check_out - record.check_in
        recent_records_with_duration.append({
            "record": record,
            "duration_formatted": format_timedelta(duration)
        })
    return render_template(
        "admin_dashboard.html",
        user_count=total_users,
        active_user_count=active_users,
        recent_records=recent_records_with_duration
    )

@admin_bp.route("/users")
@admin_required
def manage_users():
    users = User.query.order_by(User.username).all()
    return render_template("manage_users.html", users=users)

@admin_bp.route("/users/add", methods=["GET", "POST"])
@admin_required
def add_user():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        full_name = request.form.get("full_name")
        email = request.form.get("email")
        is_admin = request.form.get("is_admin") == "on"
        if not username or not password or not full_name or not email:
            flash("Todos los campos son obligatorios.", "danger")
            return render_template("user_form.html", user=None, action="add", form_data=request.form)
        existing_user = User.query.filter((User.username == username) | (User.email == email)).first()
        if existing_user:
            flash("El nombre de usuario o el correo electrónico ya existen.", "danger")
            return render_template("user_form.html", user=None, action="add", form_data=request.form)
        new_user = User(
            username=username,
            full_name=full_name,
            email=email,
            is_admin=is_admin,
            is_active=True
        )
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        flash(f"Usuario {username} creado exitosamente.", "success")
        return redirect(url_for("admin.manage_users"))
    return render_template("user_form.html", user=None, action="add")

@admin_bp.route("/users/edit/<int:user_id>", methods=["GET", "POST"])
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    if request.method == "POST":
        if user.id == session.get("user_id") and (
            (request.form.get("is_admin") == "on" and not user.is_admin) or 
            (request.form.get("is_active") == "on" and not user.is_active)
        ):
            flash("No puedes modificar tu propio estado de administrador o actividad usando este formulario.", "danger")
            return redirect(url_for("admin.edit_user", user_id=user_id))
        new_username = request.form.get("username").strip()
        new_email    = request.form.get("email").strip()
        if new_username != user.username:
            exists = User.query.filter(User.username == new_username, User.id != user.id).first()
            if exists:
                flash("El nuevo nombre de usuario ya existe.", "danger")
                return render_template("user_form.html", user=user, action="edit", form_data=request.form)
            user.username = new_username
        if new_email != user.email:
            exists = User.query.filter(User.email == new_email, User.id != user.id).first()
            if exists:
                flash("El nuevo correo electrónico ya existe.", "danger")
                return render_template("user_form.html", user=user, action="edit", form_data=request.form)
            user.email = new_email
        user.full_name = request.form.get("full_name")
        if user.id != session.get("user_id"):
            user.is_admin  = (request.form.get("is_admin") == "on")
            user.is_active = (request.form.get("is_active") == "on")
        new_password = request.form.get("password")
        if new_password:
            user.set_password(new_password)
        db.session.commit()
        flash(f"Usuario {user.username} actualizado exitosamente.", "success")
        return redirect(url_for("admin.manage_users"))
    return render_template("user_form.html", user=user, action="edit")

@admin_bp.route("/users/delete/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == session.get("user_id"):
        flash("No puedes eliminar tu propia cuenta.", "danger")
        return redirect(url_for("admin.manage_users"))
    db.session.delete(user)
    db.session.commit()
    flash("Usuario eliminado correctamente.", "success")
    return redirect(url_for("admin.manage_users"))

@admin_bp.route("/users/toggle_active/<int:user_id>", methods=["POST"])
@admin_required
def toggle_user_active(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == session.get("user_id"):
        flash("No puedes desactivar tu propia cuenta.", "danger")
        return redirect(url_for("admin.manage_users"))
    user.is_active = not user.is_active
    db.session.commit()
    status = "activado" if user.is_active else "desactivado"
    flash(f"Usuario {user.username} ha sido {status}.", "info")
    return redirect(url_for("admin.manage_users"))

@admin_bp.route("/records")
@admin_required
def manage_records():
    records = (
        TimeRecord.query
        .join(User, TimeRecord.user_id == User.id)
        .order_by(TimeRecord.id.desc())
        .all()
    )
    def format_timedelta(td):
        if td is None:
            return "-"
        total_seconds = int(td.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"
    records_with_duration = []
    for record in records:
        duration = None
        if record.check_in and record.check_out:
            duration = record.check_out - record.check_in
        records_with_duration.append({
            "record": record,
            "duration_formatted": format_timedelta(duration)
        })
    return render_template("manage_records.html", records=records_with_duration)

@admin_bp.route("/records/edit/<int:record_id>", methods=["GET", "POST"])
@admin_required
def edit_record(record_id):
    record = TimeRecord.query.get_or_404(record_id)
    if request.method == "POST":
        try:
            check_in_str = request.form.get("check_in")
            check_out_str = request.form.get("check_out")
            date_str = request.form.get("date")
            notes = request.form.get("notes")
            record.date = datetime.strptime(date_str, "%Y-%m-%d").date()
            record.check_in = datetime.strptime(f"{date_str} {check_in_str}", "%Y-%m-%d %H:%M:%S") if check_in_str else None
            record.check_out = datetime.strptime(f"{date_str} {check_out_str}", "%Y-%m-%d %H:%M:%S") if check_out_str else None
            record.notes = notes
            record.modified_by = session.get("user_id")
            if record.check_in and record.check_out and record.check_out < record.check_in:
                flash("La hora de salida no puede ser anterior a la hora de entrada.", "danger")
                return render_template("record_form.html", record=record, form_data=request.form)
            db.session.commit()
            flash(f"Registro del {record.date.strftime('%Y-%m-%d')} para {record.user.username} actualizado.", "success")
            return redirect(url_for("admin.manage_records"))
        except ValueError:
            flash("Formato de fecha/hora inválido. Use YYYY-MM-DD y HH:MM:SS.", "danger")
            return render_template("record_form.html", record=record)
                                   