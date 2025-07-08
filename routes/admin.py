from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, session, jsonify
)
from functools import wraps
from datetime import datetime, date, timedelta
from models.models import User, TimeRecord, EmployeeStatus
from models.database import db

admin_bp = Blueprint(
    "admin", __name__,
    template_folder="../templates",
    url_prefix="/admin"
)

# --------------------------------------------------------------------
#  UTILIDADES
# --------------------------------------------------------------------
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Acceso no autorizado.", "danger")
            return redirect(url_for("auth.login"))
        user = User.query.get(session.get("user_id"))
        if not user or not user.is_admin:
            session.clear()
            flash("Sin permisos de administrador.", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function

def format_timedelta(td):
    if td is None:
        return "-"
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}"

# --------------------------------------------------------------------
#  DASHBOARD
# --------------------------------------------------------------------
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

    records = (
        TimeRecord.query
        .join(User, TimeRecord.user_id == User.id)
        .filter(
            TimeRecord.date >= start_of_week,
            TimeRecord.date <= end_of_week,
            User.is_admin == False
        )
        .order_by(TimeRecord.date.asc(), TimeRecord.check_in.asc())
        .all()
    )

    week_acc, records_with_accum = {}, []
    for rec in records:
        uid = rec.user_id
        weekly_secs = rec.user.weekly_hours * 3600 if rec.user.weekly_hours else 0
        dur = rec.check_out - rec.check_in if rec.check_in and rec.check_out else None
        secs = dur.total_seconds() if dur else 0
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

    records_with_accum.reverse()

    return render_template(
        "admin_dashboard.html",
        user_count=total_users,
        active_user_count=active_users,
        recent_records=records_with_accum
    )

# --------------------------------------------------------------------
#  USUARIOS
# --------------------------------------------------------------------
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
        centro        = request.form.get("centro") or None
        categoria     = request.form.get("categoria") or None

        if not all([username, password, full_name, email]) or weekly_hours is None:
            flash("Todos los campos son obligatorios.", "danger")
            return render_template("user_form.html", user=None, action="add",
                                   form_data=request.form)

        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash("El nombre de usuario o el correo electrónico ya existen.", "danger")
            return render_template("user_form.html", user=None, action="add",
                                   form_data=request.form)

        new_user = User(
            username      = username,
            full_name     = full_name,
            email         = email,
            is_admin      = is_admin,
            is_active     = True,
            weekly_hours  = weekly_hours,
            centro        = centro,
            categoria     = categoria
        )
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        flash("Usuario creado correctamente.", "success")
        return redirect(url_for("admin.manage_users"))

    return render_template("user_form.html", user=None, action="add")

@admin_bp.route("/users/edit/<int:user_id>", methods=["GET", "POST"])
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        if user.id == session.get("user_id") and (
            (request.form.get("is_admin")  == "on" and not user.is_admin) or
            (request.form.get("is_active") == "on" and not user.is_active)
        ):
            flash("No puedes cambiar tus propios permisos.", "danger")
            return redirect(url_for("admin.edit_user", user_id=user_id))

        # username / email (únicos)
        new_username = request.form.get("username").strip()
        new_email    = request.form.get("email").strip()

        if new_username != user.username and \
           User.query.filter(User.username == new_username, User.id != user.id).first():
            flash("El nuevo nombre de usuario ya existe.", "danger")
            return render_template("user_form.html", user=user, action="edit",
                                   form_data=request.form)
        if new_email != user.email and \
           User.query.filter(User.email == new_email, User.id != user.id).first():
            flash("El nuevo correo electrónico ya existe.", "danger")
            return render_template("user_form.html", user=user, action="edit",
                                   form_data=request.form)

        # campos simples
        user.username      = new_username
        user.email         = new_email
        user.full_name     = request.form.get("full_name")
        user.weekly_hours  = request.form.get("weekly_hours", type=int)
        user.centro        = request.form.get("centro") or None
        user.categoria     = request.form.get("categoria") or None

        if user.id != session.get("user_id"):
            user.is_admin  = request.form.get("is_admin")  == "on"
            user.is_active = request.form.get("is_active") == "on"

        pw = request.form.get("password")
        if pw:
            user.set_password(pw)

        db.session.commit()
        flash("Usuario actualizado.", "success")
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
    flash("Usuario eliminado.", "success")
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
    flash(
        f"Usuario {user.username} {'activado' if user.is_active else 'desactivado'}.",
        "info"
    )
    return redirect(url_for("admin.manage_users"))

# --------------------------------------------------------------------
#  REGISTROS 
# --------------------------------------------------------------------
@admin_bp.route("/records")
@admin_required
def manage_records():
    # Página actual (semana): 1 = esta semana, 2 = anterior, etc.
    page = request.args.get("page", type=int, default=1)
    today = date.today()
    # Lunes de la semana actual
    start_of_current = today - timedelta(days=today.weekday())
    # Calcular la semana a mostrar según el número de página
    week_offset = (page - 1) * 7
    start_of_week = start_of_current - timedelta(days=week_offset)
    end_of_week = start_of_week + timedelta(days=6)

    # Buscar registros solo de esa semana
    recs = (
        TimeRecord.query
        .join(User, TimeRecord.user_id == User.id)
        .filter(
            TimeRecord.date >= start_of_week,
            TimeRecord.date <= end_of_week,
            User.is_admin == False,
            TimeRecord.check_out.isnot(None)
        )
        .order_by(TimeRecord.user_id, TimeRecord.date.asc(), TimeRecord.check_in.asc())
        .all()
    )

    # Lógica de acumulados (igual que antes)
    weekly_acc = {}
    enriched = []
    for rec in recs:
        uid = rec.user_id
        # Lunes de la semana correspondiente
        sow = rec.date - timedelta(days=rec.date.weekday())
        sow_str = sow.strftime('%Y-%m-%d')
        wh_secs = rec.user.weekly_hours * 3600 if rec.user.weekly_hours else 0

        if uid not in weekly_acc:
            weekly_acc[uid] = {}
        if sow_str not in weekly_acc[uid]:
            weekly_acc[uid][sow_str] = 0

        dur = rec.check_out - rec.check_in if rec.check_in and rec.check_out else None
        secs = dur.total_seconds() if dur else 0

        weekly_acc[uid][sow_str] += secs
        curr_week_total = weekly_acc[uid][sow_str]
        rem = wh_secs - curr_week_total

        enriched.append({
            "record": rec,
            "duration_formatted": format_timedelta(dur),
            "remaining": format_timedelta(timedelta(seconds=abs(int(rem)))),
            "is_over": rem < 0
        })

    # ¿Hay una semana anterior en la base de datos?
    earliest_record = TimeRecord.query.order_by(TimeRecord.date.asc()).first()
    has_next = False
    if earliest_record:
        first_week = earliest_record.date - timedelta(days=earliest_record.date.weekday())
        has_next = start_of_week > first_week

    # Mostramos la semana más reciente primero
    enriched = enriched[::-1]

    return render_template(
        "manage_records.html",
        records=enriched,
        page=page,
        has_next=has_next
    )

@admin_bp.route("/records/edit/<int:record_id>", methods=["GET", "POST"])
@admin_required
def edit_record(record_id):
    record = TimeRecord.query.get_or_404(record_id)
    if request.method == "POST":
        try:
            ds = request.form.get("date")
            ci = request.form.get("check_in")
            co = request.form.get("check_out")

            record.date      = datetime.strptime(ds, "%Y-%m-%d").date()
            record.check_in  = datetime.strptime(f"{ds} {ci}", "%Y-%m-%d %H:%M:%S") if ci else None
            record.check_out = datetime.strptime(f"{ds} {co}", "%Y-%m-%d %H:%M:%S") if co else None
            record.notes     = request.form.get("notes")
            record.modified_by = session.get("user_id")

            if record.check_in and record.check_out and record.check_out < record.check_in:
                flash("La salida no puede ser anterior a la entrada.", "danger")
                return render_template("record_form.html", record=record,
                                       form_data=request.form)

            db.session.commit()
            flash("Registro actualizado.", "success")
            return redirect(url_for("admin.manage_records"))

        except ValueError:
            flash("Formato fecha/hora inválido.", "danger")
    return render_template("record_form.html", record=record)

@admin_bp.route("/records/delete/<int:record_id>", methods=["POST"])
@admin_required
def delete_record(record_id):
    record = TimeRecord.query.get_or_404(record_id)
    db.session.delete(record)
    db.session.commit()
    flash("Registro eliminado.", "success")
    return redirect(url_for("admin.manage_records"))

# --------------------------------------------------------------------
#  CALENDARIO GLOBAL + API 
# --------------------------------------------------------------------
@admin_bp.route("/calendar")
@admin_required
def admin_calendar():
    return render_template("admin_calendar.html")

@admin_bp.route("/api/events")
@admin_required
def api_events():
    """Eventos para el calendario global."""
    user_id = request.args.get("user_id", type=int)
    start   = request.args.get("start")
    end     = request.args.get("end")
    status  = request.args.get("status")
    centro  = request.args.get("centro")

    # ==== Cambios para manejo correcto de fechas ====
    start_date = None
    end_date = None
    if start:
        try:
            if 'T' in start:
                start_date = datetime.fromisoformat(start.replace('Z', '')).date()
            else:
                start_date = datetime.strptime(start, "%Y-%m-%d").date()
        except Exception:
            start_date = None
    if end:
        try:
            if 'T' in end:
                end_date = datetime.fromisoformat(end.replace('Z', '')).date()
            else:
                end_date = datetime.strptime(end, "%Y-%m-%d").date()
        except Exception:
            end_date = None

    q = EmployeeStatus.query.join(User).filter(User.is_admin == False)

    if user_id:
        q = q.filter(EmployeeStatus.user_id == user_id)
    if start_date:
        q = q.filter(EmployeeStatus.date >= start_date)
    if end_date:
        q = q.filter(EmployeeStatus.date <= end_date)
    if status:
        q = q.filter(EmployeeStatus.status == status)
    if centro:
        q = q.filter(User.centro == centro)

    events = [
        {
            "id"   : es.id,
            "title": f"{es.status} - {es.user.full_name or es.user.username}",
            "start": es.date.isoformat(),
            "color": {
                "Trabajado" : "#60a5fa",
                "Baja"      : "#f87171",
                "Ausente"   : "#fbbf24",
                "Vacaciones": "#34d399"
            }.get(es.status, "#9ca3af"),
            "extendedProps": {
                "notes": es.notes,
                "username": es.user.full_name or es.user.username,
                "category": es.user.categoria
            },
            "allDay": True
        }
        for es in q.all()
    ]
    return jsonify(events)

@admin_bp.route("/api/employees")
@admin_required
def api_employees():
    centro = request.args.get("centro")
    query = User.query.filter_by(is_admin=False)
    
    if centro:
        query = query.filter(User.centro == centro)
    
    employees = query.order_by(User.full_name).all()
    return jsonify([
        {"id": u.id, "username": u.username, "full_name": u.full_name}
        for u in employees
    ])

@admin_bp.route("/api/centro_info")
@admin_required
def api_centro_info():
    centro = request.args.get("centro")
    users = User.query.filter_by(is_admin=False)
    if centro:
        users = users.filter(User.centro == centro)
    users = users.all()
    categorias = sorted(set(u.categoria for u in users if u.categoria))
    horas = sorted(set(u.weekly_hours for u in users if u.weekly_hours is not None))
    return jsonify({
        "usuarios": [{"id": u.id, "username": u.username, "full_name": u.full_name} for u in users],
        "categorias": categorias,
        "horas": horas
    })

# --------------------------------------------------------------------
#  FICHA INDIVIDUAL (rangos fechas)
# --------------------------------------------------------------------
@admin_bp.route("/employees/<int:user_id>/status", methods=["GET", "POST"])
@admin_required
def manage_employee_status(user_id):
    """
    Alta / actualización de estados del empleado.
    • Se admite rango de fechas (start_date / end_date)
    • Solo se guarda 'status' + 'notes'  → sin categoría
    • Si ya existe un estado para ese día, se sobreescribe
    """
    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        start_str = request.form.get("start_date")
        end_str   = request.form.get("end_date") or start_str
        status    = request.form.get("status", "")
        notes     = request.form.get("notes", "")

        if not start_str:
            flash("Indica la fecha de inicio.", "danger")
            return redirect(url_for("admin.manage_employee_status", user_id=user_id))

        try:
            start = datetime.strptime(start_str, "%Y-%m-%d").date()
            end   = datetime.strptime(end_str,   "%Y-%m-%d").date()
        except ValueError:
            flash("Formato de fecha inválido.", "danger")
            return redirect(url_for("admin.manage_employee_status", user_id=user_id))

        if end < start:
            flash("La fecha final no puede ser anterior a la inicial.", "danger")
            return redirect(url_for("admin.manage_employee_status", user_id=user_id))

        delta = (end - start).days + 1
        for i in range(delta):
            day = start + timedelta(days=i)
            existing = EmployeeStatus.query.filter_by(
                user_id=user_id, date=day
            ).first()
            if existing:
                existing.status = status
                existing.notes  = notes
            else:
                db.session.add(EmployeeStatus(
                    user_id = user_id,
                    date    = day,
                    status  = status,
                    notes   = notes
                ))
        db.session.commit()
        flash("Estado guardado.", "success")
        return redirect(url_for("admin.manage_employee_status", user_id=user_id))

    return render_template("employee_status.html", user=user)

# --------------------------------------------------------------------
#  ELIMINAR ESTADO INDIVIDUAL DE UN EMPLEADO
# --------------------------------------------------------------------
@admin_bp.route("/employees/<int:user_id>/status/delete/<int:status_id>", methods=["POST"])
@admin_required
def delete_employee_status(user_id, status_id):
    status = EmployeeStatus.query.get_or_404(status_id)
    db.session.delete(status)
    db.session.commit()
    flash("Estado eliminado correctamente.", "success")
    return redirect(url_for("admin.manage_employee_status", user_id=user_id))

@admin_bp.route("/employees/<int:user_id>/status/edit/<int:status_id>", methods=["POST"])
@admin_required
def edit_employee_status(user_id, status_id):
    status = EmployeeStatus.query.get_or_404(status_id)
    data = request.get_json()
    status.status = data.get("status")
    status.notes = data.get("notes")
    db.session.commit()
    return jsonify({"ok": True})

# --------------------------------------------------------------------
#  FICHAS ABIERTAS DE EMPLEADOS
# --------------------------------------------------------------------
@admin_bp.route("/open_records", methods=["GET", "POST"])
@admin_required
def open_records():
    open_records = (
        TimeRecord.query
        .join(User, TimeRecord.user_id == User.id)
        .filter(TimeRecord.check_in.isnot(None), TimeRecord.check_out.is_(None))
        .all()
    )

    if request.method == "POST":
        record_id = request.form.get("record_id")
        close_time = request.form.get("close_time")
        record = TimeRecord.query.get(record_id)
        if record and close_time:
            try:
                record.check_out = datetime.strptime(close_time, "%Y-%m-%dT%H:%M")
                db.session.commit()
                flash("Registro cerrado correctamente.", "success")
            except Exception as e:
                flash(f"Error al cerrar: {e}", "danger")
        return redirect(url_for("admin.open_records"))

    return render_template("open_records.html", open_records=open_records)
