from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from functools import wraps
from models.models import User, TimeRecord
from models.database import db
from werkzeug.security import generate_password_hash
from datetime import datetime, date, timedelta
from models.models import User, TimeRecord, EmployeeStatus


admin_bp = Blueprint(
    "admin", __name__,
    template_folder="../templates",
    url_prefix="/admin"
)

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

def format_timedelta(td):
    if td is None:
        return "-"
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"

@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    total_users = User.query.filter_by(is_admin=False).count()
    active_users = (
        db.session.query(TimeRecord.user_id)
        .filter(TimeRecord.check_in.isnot(None), TimeRecord.check_out.is_(None))
        .distinct()
        .count()
    )

    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=6)

    # Traer TODOS los fichajes de la semana, ordenados de antiguo a nuevo (ascendente)
    records = (
        TimeRecord.query
        .join(User, TimeRecord.user_id == User.id)
        .filter(
            TimeRecord.date >= start_of_week,
            TimeRecord.date <= end_of_week,
        )
        .order_by(TimeRecord.date.asc(), TimeRecord.check_in.asc())
        .all()
    )

    # Acumular tiempo semanal empleado a empleado
    week_acc = {}
    records_with_accum = []

    for rec in records:
        uid = rec.user_id
        weekly_secs = rec.user.weekly_hours * 3600 if rec.user.weekly_hours else 0

        # Duración solo si tiene salida
        dur = rec.check_out - rec.check_in if rec.check_in and rec.check_out else None
        secs = dur.total_seconds() if dur else 0

        # Acumula solo si registro está cerrado
        prev = week_acc.get(uid, 0)
        curr = prev + secs if rec.check_out else prev
        week_acc[uid] = curr

        rem = weekly_secs - curr

        records_with_accum.append({
            "record": rec,
            "duration_formatted": format_timedelta(dur) if dur else "-",
            "remaining_formatted": format_timedelta(timedelta(seconds=abs(int(rem)))),
            "is_over": rem < 0,
            "is_open": rec.check_in and not rec.check_out
        })

    # Mostramos lo más reciente arriba
    records_with_accum = records_with_accum[::-1]

    return render_template(
        "admin_dashboard.html",
        user_count=total_users,
        active_user_count=active_users,
        recent_records=records_with_accum
    )

# ---- El resto del archivo sigue igual ----

@admin_bp.route("/users")
@admin_required
def manage_users():
    users = User.query.order_by(User.username).all()
    return render_template("manage_users.html", users=users)

@admin_bp.route("/users/add", methods=["GET", "POST"])
@admin_required
def add_user():
    if request.method == "POST":
        username      = request.form.get("username")
        password      = request.form.get("password")
        full_name     = request.form.get("full_name")
        email         = request.form.get("email")
        is_admin      = request.form.get("is_admin") == "on"
        weekly_hours  = request.form.get("weekly_hours", type=int)

        if not all([username, password, full_name, email]) or weekly_hours is None:
            flash("Todos los campos son obligatorios.", "danger")
            return render_template(
                "user_form.html",
                user=None, action="add",
                form_data=request.form
            )

        if User.query.filter((User.username==username)|(User.email==email)).first():
            flash("El nombre de usuario o el correo electrónico ya existen.", "danger")
            return render_template(
                "user_form.html",
                user=None, action="add",
                form_data=request.form
            )

        new_user = User(
            username=username,
            full_name=full_name,
            email=email,
            is_admin=is_admin,
            is_active=True,
            weekly_hours=weekly_hours
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
            (request.form.get("is_admin")=="on" and not user.is_admin) or
            (request.form.get("is_active")=="on" and not user.is_active)
        ):
            flash("No puedes modificar tu propio estado de administrador o actividad.", "danger")
            return redirect(url_for("admin.edit_user", user_id=user_id))

        # campos básicos
        new_username = request.form.get("username").strip()
        new_email    = request.form.get("email").strip()
        if new_username != user.username:
            if User.query.filter(User.username==new_username, User.id!=user.id).first():
                flash("El nuevo nombre de usuario ya existe.", "danger")
                return render_template("user_form.html", user=user, action="edit", form_data=request.form)
            user.username = new_username

        if new_email != user.email:
            if User.query.filter(User.email==new_email, User.id!=user.id).first():
                flash("El nuevo correo electrónico ya existe.", "danger")
                return render_template("user_form.html", user=user, action="edit", form_data=request.form)
            user.email = new_email

        # full_name y weekly_hours
        user.full_name    = request.form.get("full_name")
        user.weekly_hours = request.form.get("weekly_hours", type=int)

        if user.id != session.get("user_id"):
            user.is_admin  = (request.form.get("is_admin")=="on")
            user.is_active = (request.form.get("is_active")=="on")

        pw = request.form.get("password")
        if pw:
            user.set_password(pw)

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
    recs = (
        TimeRecord.query
        .join(User, TimeRecord.user_id == User.id)
        .filter(TimeRecord.check_out.isnot(None))
        .order_by(TimeRecord.date.asc(), TimeRecord.check_in.asc())
        .all()
    )

    enriched = []
    week_acc = {}
    for rec in recs:
        dur = None
        if rec.check_in and rec.check_out:
            dur = rec.check_out - rec.check_in
        secs = dur.total_seconds() if dur else 0

        uid = rec.user_id
        sow = rec.date - timedelta(days=rec.date.weekday())
        eow = sow + timedelta(days=6)
        if sow <= rec.date <= eow:
            prev = week_acc.get(uid, 0)
            curr = prev + secs
            week_acc[uid] = curr
        else:
            week_acc[uid] = secs

        wh_secs = rec.user.weekly_hours * 3600
        rem = wh_secs - week_acc[uid]

        enriched.append({
            "record": rec,
            "duration_formatted": format_timedelta(dur),
            "remaining": format_timedelta(timedelta(seconds=abs(int(rem)))),
            "is_over": rem < 0
        })

    enriched = enriched[::-1]

    return render_template("manage_records.html", records=enriched)

@admin_bp.route("/records/edit/<int:record_id>", methods=["GET", "POST"])
@admin_required
def edit_record(record_id):
    record = TimeRecord.query.get_or_404(record_id)
    if request.method == "POST":
        try:
            ci = request.form.get("check_in")
            co = request.form.get("check_out")
            ds = request.form.get("date")
            record.date      = datetime.strptime(ds, "%Y-%m-%d").date()
            record.check_in  = datetime.strptime(f"{ds} {ci}", "%Y-%m-%d %H:%M:%S") if ci else None
            record.check_out = datetime.strptime(f"{ds} {co}", "%Y-%m-%d %H:%M:%S") if co else None
            record.notes     = request.form.get("notes")
            record.modified_by = session.get("user_id")
            if record.check_in and record.check_out and record.check_out < record.check_in:
                flash("La hora de salida no puede ser anterior a la entrada.", "danger")
                return render_template("record_form.html", record=record, form_data=request.form)
            db.session.commit()
            flash(f"Registro actualizado para {record.user.username}.", "success")
            return redirect(url_for("admin.manage_records"))
        except ValueError:
            flash("Formato fecha/hora inválido.", "danger")
        except Exception as e:
            flash(f"Error inesperado: {e}", "danger")
    return render_template("record_form.html", record=record)

@admin_bp.route("/records/delete/<int:record_id>", methods=["POST"])
@admin_required
def delete_record(record_id):
    record = TimeRecord.query.get_or_404(record_id)
    db.session.delete(record)
    db.session.commit()
    flash("Registro eliminado correctamente.", "success")
    return redirect(url_for("admin.manage_records"))

@admin_bp.route("/calendar")
@admin_required
def admin_calendar():
    return render_template("admin_calendar.html")

from flask import jsonify

@admin_bp.route("/api/events")
@admin_required
def api_events():
    user_id = request.args.get("user_id", type=int)
    start = request.args.get("start")
    end = request.args.get("end")
    query = EmployeeStatus.query.join(User)
    if user_id:
        query = query.filter(EmployeeStatus.user_id == user_id)
    if start:
        query = query.filter(EmployeeStatus.date >= start)
    if end:
        query = query.filter(EmployeeStatus.date <= end)
    events = [
        {
            "id": es.id,
            "title": es.status,
            "start": es.date.isoformat(),
            "color": {
                "Trabajado": "#60a5fa",
                "Baja": "#f87171",
                "Ausencia": "#fbbf24",
                "Vacaciones": "#34d399"
            }.get(es.status, "#9c9c9c"),
            "extendedProps": {
                "category": es.category,
                "notes": es.notes
            },
            "allDay": True
        }
        for es in query.all()
    ]
    return jsonify(events)
