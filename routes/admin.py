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

# Centro del admin actual (None implica super admin con acceso global)

def get_admin_centro():
    uid = session.get("user_id")
    if not uid:
        return None
    u = User.query.get(uid)
    # Considerar "-- Sin categoría --" como sin centro (super admin)
    if u and u.is_admin and u.centro and u.centro != "-- Sin categoría --":
        return u.centro
    return None

# Helpers de permisos

def _current_user():
    uid = session.get("user_id")
    return User.query.get(uid) if uid else None

def is_super_admin_user(u: User | None):
    return bool(u and u.is_admin and (not u.centro or u.centro == "-- Sin categoría --"))

def can_grant_admin():
    u = _current_user()
    # Permitidos explícitos + cualquier super admin actual
    return bool(is_super_admin_user(u) or (u and u.username in ("Valle", "aDmin")))

def can_grant_super_admin():
    # Solo un super admin actual puede otorgar super admin
    return is_super_admin_user(_current_user())

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
    # Centro del admin (None => super admin con acceso global)
    centro_admin = get_admin_centro()

    # Filtros opcionales
    filtro_centro = request.args.get("centro", type=str, default="")
    filtro_categoria = request.args.get("categoria", type=str, default="")

    # Totales de usuarios (limitados por centro si aplica)
    user_q = User.query.filter_by(is_admin=False)
    if centro_admin:
        user_q = user_q.filter(User.centro == centro_admin)
    elif filtro_centro:
        user_q = user_q.filter(User.centro == filtro_centro)
    if filtro_categoria:
        user_q = user_q.filter(User.categoria == filtro_categoria)
    total_users = user_q.count()

    # Usuarios activos con fichaje abierto (limitados por centro si aplica)
    active_q = (
        db.session.query(TimeRecord.user_id)
        .join(User, TimeRecord.user_id == User.id)
        .filter(TimeRecord.check_in.isnot(None), TimeRecord.check_out.is_(None))
    )
    if centro_admin:
        active_q = active_q.filter(User.centro == centro_admin)
    elif filtro_centro:
        active_q = active_q.filter(User.centro == filtro_centro)
    if filtro_categoria:
        active_q = active_q.filter(User.categoria == filtro_categoria)
    active_users = active_q.distinct().count()

    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=6)

    q = (
        TimeRecord.query
        .join(User, TimeRecord.user_id == User.id)
        .filter(
            TimeRecord.date >= start_of_week,
            TimeRecord.date <= end_of_week,
            User.is_admin == False
        )
    )
    if centro_admin:
        q = q.filter(User.centro == centro_admin)
    elif filtro_centro:
        q = q.filter(User.centro == filtro_centro)
    if filtro_categoria:
        q = q.filter(User.categoria == filtro_categoria)

    records = q.order_by(TimeRecord.date.asc(), TimeRecord.check_in.asc()).all()

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

    # Opciones dinámicas de centros y categorías (limitadas por centro del admin si aplica)
    centros_q = User.query
    if centro_admin:
        centros_q = centros_q.filter(User.centro == centro_admin)
    centros = [c[0] for c in centros_q.with_entities(User.centro).filter(User.centro.isnot(None)).distinct().order_by(User.centro).all()]

    categorias_q = User.query
    if centro_admin:
        categorias_q = categorias_q.filter(User.centro == centro_admin)
    categorias = [c[0] for c in categorias_q.with_entities(User.categoria).filter(User.categoria.isnot(None)).distinct().order_by(User.categoria).all()]

    return render_template(
        "admin_dashboard.html",
        user_count=total_users,
        active_user_count=active_users,
        recent_records=records_with_accum,
        centros=centros,
        categorias=categorias
    )

# --------------------------------------------------------------------
#  USUARIOS
# --------------------------------------------------------------------
@admin_bp.route("/users")
@admin_required
def manage_users():
    centro_admin = get_admin_centro()

    # Filtros opcionales
    filtro_centro = request.args.get("centro", type=str, default="")
    filtro_categoria = request.args.get("categoria", type=str, default="")
    search_query = request.args.get("search", type=str, default="")

    q = User.query
    if centro_admin:
        q = q.filter(User.centro == centro_admin)
    elif filtro_centro:
        q = q.filter(User.centro == filtro_centro)
    if filtro_categoria:
        q = q.filter(User.categoria == filtro_categoria)
    if search_query:
        # Buscar en nombre completo (nombre y apellidos) y username
        q = q.filter(
            (User.full_name.ilike(f"%{search_query}%")) | 
            (User.username.ilike(f"%{search_query}%"))
        )

    users = q.order_by(User.username).all()

    # Opciones dinámicas de centros y categorías (limitadas por centro del admin si aplica)
    centros_q = User.query
    if centro_admin:
        centros_q = centros_q.filter(User.centro == centro_admin)
    centros = [c[0] for c in centros_q.with_entities(User.centro).filter(User.centro.isnot(None)).distinct().order_by(User.centro).all()]

    categorias_q = User.query
    if centro_admin:
        categorias_q = categorias_q.filter(User.centro == centro_admin)
    categorias = [c[0] for c in categorias_q.with_entities(User.categoria).filter(User.categoria.isnot(None)).distinct().order_by(User.categoria).all()]

    return render_template("manage_users.html", users=users, centros=centros, categorias=categorias, centro_admin=centro_admin)

@admin_bp.route("/users/add", methods=["GET", "POST"])
@admin_required
def add_user():
    if request.method == "POST":
        username      = request.form.get("username")
        password      = request.form.get("password")
        full_name     = request.form.get("full_name")
        email         = request.form.get("email")
        # Solo algunos usuarios pueden crear administradores/super admin
        req_is_admin  = request.form.get("is_admin") == "on"
        weekly_hours  = request.form.get("weekly_hours", type=int)
        centro        = request.form.get("centro") or None
        categoria     = request.form.get("categoria") or None
        hire_date_str = request.form.get("hire_date")
        termination_date_str = request.form.get("termination_date")
        
        # Convertir fechas si están presentes
        hire_date = None
        termination_date = None
        try:
            if hire_date_str:
                hire_date = datetime.strptime(hire_date_str, "%Y-%m-%d").date()
            if termination_date_str:
                termination_date = datetime.strptime(termination_date_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Formato de fecha inválido.", "danger")
            return render_template("user_form.html", user=None, action="add",
                                   form_data=request.form, centro_admin=get_admin_centro())
        # Super admin: centro vacío o "-- Sin categoría --"
        req_is_super  = (centro in (None, "-- Sin categoría --"))
        is_admin      = req_is_admin if can_grant_admin() else False
        if req_is_super and not can_grant_super_admin():
            # Si no puede conceder super admin, forzamos un centro válido
            if centro in (None, "-- Sin categoría --"):
                centro = "-- Sin categoría --"  # permitido como valor, pero no super admin salvo privilegio
        # Nota: ser super admin depende de is_admin y del centro especial (None/"-- Sin categoría --")

        if not all([username, password, full_name, email]) or weekly_hours is None:
            flash("Todos los campos son obligatorios.", "danger")
            return render_template("user_form.html", user=None, action="add",
                                   form_data=request.form, centro_admin=get_admin_centro())

        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash("El nombre de usuario o el correo electrónico ya existen.", "danger")
            return render_template("user_form.html", user=None, action="add",
                                   form_data=request.form, centro_admin=get_admin_centro())

        new_user = User(
            username         = username,
            full_name        = full_name,
            email            = email,
            is_admin         = is_admin,
            is_active        = True,
            weekly_hours     = weekly_hours,
            centro           = centro,
            categoria        = categoria,
            hire_date        = hire_date,
            termination_date = termination_date
        )
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        flash("Usuario creado correctamente.", "success")
        return redirect(url_for("admin.manage_users"))

    return render_template("user_form.html", user=None, action="add", centro_admin=get_admin_centro())

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

        # Si el que edita no tiene permiso de conceder admin, ignorar cambios a is_admin
        requested_is_admin = (request.form.get("is_admin") == "on")
        if can_grant_admin():
            user.is_admin = requested_is_admin
        # Si no, se respeta el estado actual de user.is_admin sin cambios

        # username / email (únicos)
        new_username = request.form.get("username").strip()
        new_email    = request.form.get("email").strip()

        if new_username != user.username and \
           User.query.filter(User.username == new_username, User.id != user.id).first():
            flash("El nuevo nombre de usuario ya existe.", "danger")
            return render_template("user_form.html", user=user, action="edit",
                                   form_data=request.form, centro_admin=get_admin_centro())
        if new_email != user.email and \
           User.query.filter(User.email == new_email, User.id != user.id).first():
            flash("El nuevo correo electrónico ya existe.", "danger")
            return render_template("user_form.html", user=user, action="edit",
                                   form_data=request.form, centro_admin=get_admin_centro())

        # campos simples
        user.username      = new_username
        user.email         = new_email
        user.full_name     = request.form.get("full_name")
        user.weekly_hours  = request.form.get("weekly_hours", type=int)
        user.centro        = request.form.get("centro") or None
        user.categoria     = request.form.get("categoria") or None
        
        # Fechas de alta y baja
        hire_date_str = request.form.get("hire_date")
        termination_date_str = request.form.get("termination_date")
        
        try:
            user.hire_date = datetime.strptime(hire_date_str, "%Y-%m-%d").date() if hire_date_str else None
            user.termination_date = datetime.strptime(termination_date_str, "%Y-%m-%d").date() if termination_date_str else None
        except ValueError:
            flash("Formato de fecha inválido.", "danger")
            return render_template("user_form.html", user=user, action="edit",
                                   form_data=request.form, centro_admin=get_admin_centro())

        if user.id != session.get("user_id"):
            user.is_admin  = request.form.get("is_admin")  == "on"
            user.is_active = request.form.get("is_active") == "on"

        pw = request.form.get("password")
        if pw:
            user.set_password(pw)

        db.session.commit()
        flash("Usuario actualizado.", "success")
        return redirect(url_for("admin.manage_users"))

    return render_template("user_form.html", user=user, action="edit", centro_admin=get_admin_centro())

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

    # Calcular número de semana ISO y rango de días
    week_number = start_of_week.isocalendar().week

    # Nombres de meses en español
    meses_es = {
        1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
        5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
        9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'
    }
    mes_nombre = meses_es[start_of_week.month]
    week_range = f"{start_of_week.day} - {end_of_week.day} de {mes_nombre}"

    # Filtros por query params (fecha, hora, categoría, centro y búsqueda)
    date_from = request.args.get("date_from")
    date_to   = request.args.get("date_to")
    time_from = request.args.get("time_from")  # HH:MM
    time_to   = request.args.get("time_to")    # HH:MM
    categoria = request.args.get("categoria")
    filtro_centro = request.args.get("centro", type=str, default="")
    search_query = request.args.get("search", type=str, default="")

    # Buscar registros solo de esa semana + filtros
    q = (
        TimeRecord.query
        .join(User, TimeRecord.user_id == User.id)
        .filter(
            TimeRecord.date >= start_of_week,
            TimeRecord.date <= end_of_week,
            User.is_admin == False,
            TimeRecord.check_out.isnot(None)
        )
    )

    # Scope por centro del admin, si aplica. Si es super admin, permitir filtro por centro
    centro_admin = get_admin_centro()
    if centro_admin:
        q = q.filter(User.centro == centro_admin)
    elif filtro_centro:
        q = q.filter(User.centro == filtro_centro)

    # Aplicar filtros opcionales (fechas)
    ci_from = co_to = None
    try:
        if date_from:
            df = datetime.strptime(date_from, "%Y-%m-%d").date()
            q = q.filter(TimeRecord.date >= df)
        if date_to:
            dt = datetime.strptime(date_to, "%Y-%m-%d").date()
            q = q.filter(TimeRecord.date <= dt)
        if time_from:
            ci_from = datetime.strptime(time_from, "%H:%M").time()
        if time_to:
            co_to = datetime.strptime(time_to, "%H:%M").time()
    except ValueError:
        flash("Formato de fecha/hora inválido en filtros.", "warning")

    if categoria:
        q = q.filter(User.categoria == categoria)
    
    # Filtrar por búsqueda de nombre completo y username
    if search_query:
        q = q.filter(
            (User.full_name.ilike(f"%{search_query}%")) | 
            (User.username.ilike(f"%{search_query}%"))
        )

    recs = q.order_by(TimeRecord.user_id, TimeRecord.date.asc(), TimeRecord.check_in.asc()).all()

    # Filtrar por horas en Python para compatibilidad con SQLite/Postgres
    if ci_from or co_to:
        filtered = []
        for r in recs:
            ok = True
            if ci_from and (not r.check_in or r.check_in.time() < ci_from):
                ok = False
            if co_to and (not r.check_out or r.check_out.time() > co_to):
                ok = False
            if ok:
                filtered.append(r)
        recs = filtered

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

    # Calcular si es la semana actual
    is_current_week = (start_of_week == start_of_current)

    # Opciones dinámicas de centros para el select
    centros_q = User.query
    if centro_admin:
        centros_q = centros_q.filter(User.centro == centro_admin)
    centros = [c[0] for c in centros_q.with_entities(User.centro).filter(User.centro.isnot(None)).distinct().order_by(User.centro).all()]

    return render_template(
        "manage_records.html",
        records=enriched,
        page=page,
        has_next=has_next,
        week_number=week_number,
        week_range=week_range,
        is_current_week=is_current_week,
        centros=centros,
        centro_admin=centro_admin
    )

@admin_bp.route("/records/edit/<int:record_id>", methods=["GET", "POST"])
@admin_required
def edit_record(record_id):
    record = TimeRecord.query.get_or_404(record_id)
    page = request.args.get("page", type=int, default=1)
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
            return redirect(url_for("admin.manage_records", page=page, edited=record.id))

        except ValueError:
            flash("Formato fecha/hora inválido.", "danger")
    return render_template("record_form.html", record=record, page=page)

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
    # Pasamos el centro del admin (None => super admin)
    return render_template("admin_calendar.html", centro_admin=get_admin_centro())

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

    # Scope por centro del admin (si tiene asignado)
    centro_admin = get_admin_centro()
    if centro_admin:
        q = q.filter(User.centro == centro_admin)
    elif centro:
        # Si no hay centro del admin, permitir filtrar por parámetro opcional
        q = q.filter(User.centro == centro)

    if user_id:
        q = q.filter(EmployeeStatus.user_id == user_id)
    if start_date:
        q = q.filter(EmployeeStatus.date >= start_date)
    if end_date:
        q = q.filter(EmployeeStatus.date <= end_date)
    if status:
        q = q.filter(EmployeeStatus.status == status)

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

    # Limitar por centro del admin, si aplica (super admin => None)
    centro_admin = get_admin_centro()
    if centro_admin:
        query = query.filter(User.centro == centro_admin)
    elif centro:
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

    centro_admin = get_admin_centro()
    if centro_admin:
        users = users.filter(User.centro == centro_admin)
    elif centro:
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
    # Limitar por centro del admin si aplica y excluir admins
    centro_admin = get_admin_centro()
    q = (
        TimeRecord.query
        .join(User, TimeRecord.user_id == User.id)
        .filter(
            TimeRecord.check_in.isnot(None),
            TimeRecord.check_out.is_(None),
            User.is_admin == False
        )
    )
    if centro_admin:
        q = q.filter(User.centro == centro_admin)
    open_records = q.all()

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

# --------------------------------------------------------------------
#  CIERRE AUTOMÁTICO MANUAL
# --------------------------------------------------------------------
@admin_bp.route("/close_today_records", methods=["POST"])
@admin_required
def close_today_records():
    """Manual trigger to close all open records for today"""
    try:
        from tasks.scheduler import manual_auto_close_records
        closed_count = manual_auto_close_records()
        if closed_count > 0:
            flash(f"Se cerraron automáticamente {closed_count} registros abiertos de hoy.", "success")
        else:
            flash("No hay registros abiertos para cerrar hoy.", "info")
    except ImportError:
        flash("La funcionalidad de cierre automático no está disponible.", "warning")
    except Exception as e:
        flash(f"Error al cerrar registros: {str(e)}", "danger")
    
    return redirect(url_for("admin.open_records"))
