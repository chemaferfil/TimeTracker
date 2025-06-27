from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from functools import wraps
from models.models import User, TimeRecord
from models.database import db
from werkzeug.security import generate_password_hash
from datetime import datetime, date, timedelta

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
    # 1. métricas base
    total_users = User.query.filter_by(is_admin=False).count()
    active_users = (
        db.session.query(TimeRecord.user_id)
        .filter(TimeRecord.check_in.isnot(None), TimeRecord.check_out.is_(None))
        .distinct()
        .count()
    )

    # 2. obtengo todos los registros cerrados de esta semana para **cada** empleado
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=6)

    records = (
        TimeRecord.query
        .join(User, TimeRecord.user_id == User.id)
        .filter(
            TimeRecord.date >= start_of_week,
            TimeRecord.date <= end_of_week,
            TimeRecord.check_out.isnot(None)
        )
        .order_by(TimeRecord.date, TimeRecord.check_in)
        .all()
    )

    # 3. agrupación por usuario para acumulado semanal
    #    y enriquecido fila a fila con remaining e is_over
    #    usamos dict: { user_id: segundos_acumulados }
    week_acc = {}
    recent_records_with_duration = []
    for rec in records:
        uid = rec.user_id
        # leemos la jornada semanal del empleado
        weekly_secs = rec.user.weekly_hours * 3600

        # duración de este registro
        dur = None
        if rec.check_in and rec.check_out:
            dur = rec.check_out - rec.check_in
        secs = dur.total_seconds() if dur else 0

        # acumulamos sólo para este usuario
        prev = week_acc.get(uid, 0)
        curr = prev + secs
        week_acc[uid] = curr

        # calculamos restante (o sobrante)
        rem = weekly_secs - curr
        # guardamos
        recent_records_with_duration.append({
            "record": rec,
            "duration_formatted": format_timedelta(dur),
            "remaining_formatted": format_timedelta(timedelta(seconds=abs(int(rem)))),
            "is_over": rem < 0
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
        username      = request.form.get("username")
        password      = request.form.get("password")
        full_name     = request.form.get("full_name")
        email         = request.form.get("email")
        is_admin      = request.form.get("is_admin") == "on"
        weekly_hours  = request.form.get("weekly_hours", type=int)
        category      = request.form.get("category")

        if not all([username, password, full_name, email, category]) or weekly_hours is None:
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
            weekly_hours=weekly_hours,
            category=category
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

        user.full_name    = request.form.get("full_name")
        user.weekly_hours = request.form.get("weekly_hours", type=int)
        user.category     = request.form.get("category")

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
    # obtenemos todos los registros con salida
    recs = (
        TimeRecord.query
        .join(User, TimeRecord.user_id == User.id)
        .filter(TimeRecord.check_out.isnot(None))
        .order_by(TimeRecord.check_in.desc())
        .all()
    )

    enriched = []
    # acumulado por usuario ID
    week_acc = {}
    for rec in recs:
        dur = None
        if rec.check_in and rec.check_out:
            dur = rec.check_out - rec.check_in
        secs = dur.total_seconds() if dur else 0

        uid = rec.user_id
        # rango semanal de este registro
        sow = rec.date - timedelta(days=rec.date.weekday())
        eow = sow + timedelta(days=6)
        # sólo sumamos si está dentro de su propia semana actual
        if sow <= rec.date <= eow:
            prev = week_acc.get(uid, 0)
            curr = prev + secs
            week_acc[uid] = curr
        else:
            # reinicia acumulado si cambiamos de semana
            week_acc[uid] = secs

        # cálculo remaining
        wh_secs = rec.user.weekly_hours * 3600
        rem = wh_secs - week_acc[uid]

        enriched.append({
            "record": rec,
            "duration_formatted": format_timedelta(dur),
            "remaining": format_timedelta(timedelta(seconds=abs(int(rem)))),
            "is_over": rem < 0
        })

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
def calendar():
    users = User.query.order_by(User.username).all()

    selected_user_id = request.args.get("user_id", type=int)
    start_date_str = request.args.get("start_date")
    end_date_str = request.args.get("end_date")

    start_date = (
        datetime.strptime(start_date_str, "%Y-%m-%d").date()
        if start_date_str else None
    )
    end_date = (
        datetime.strptime(end_date_str, "%Y-%m-%d").date()
        if end_date_str else None
    )

    query = TimeRecord.query
    if selected_user_id:
        query = query.filter_by(user_id=selected_user_id)
    if start_date:
        query = query.filter(TimeRecord.date >= start_date)
    if end_date:
        query = query.filter(TimeRecord.date <= end_date)

    records = query.order_by(TimeRecord.date).all()

    events = []
    for rec in records:
        if rec.check_in:
            events.append({
                "title": f"{rec.user.username} entrada",
                "start": rec.check_in.isoformat(),
            })
        if rec.check_out:
            events.append({
                "title": f"{rec.user.username} salida",
                "start": rec.check_out.isoformat(),
            })

    return render_template(
        "admin.calendar.html",
        users=users,
        categories=[],
        selected_user_id=selected_user_id,
        selected_category=None,
        start_date=start_date_str or "",
        end_date=end_date_str or "",
        events=events,
    )