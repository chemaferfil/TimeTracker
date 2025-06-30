from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, session
)
from sqlalchemy import desc, text, and_
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, date, timedelta
import calendar

from models.models import TimeRecord, User, EmployeeStatus
from models.database import db

time_bp = Blueprint("time", __name__)


# ------------------------------------------------------------------
#  UTILIDAD
# ------------------------------------------------------------------
def format_timedelta(td):
    if td is None:
        return "-"
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}"


# ------------------------------------------------------------------
#  FICHAR ENTRADA
# ------------------------------------------------------------------
@time_bp.route("/check_in", methods=["POST"])
def check_in():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    user_id = session["user_id"]

     # BUSCA REGISTRO ABIERTO
    existing_open = TimeRecord.query.filter_by(user_id=user_id, check_out=None).order_by(desc(TimeRecord.id)).first()
    if existing_open:
        # Permite al usuario cerrarlo desde aquí
        flash(f"Tienes un fichaje abierto desde {existing_open.check_in.strftime('%d-%m-%Y %H:%M:%S')}. Debes cerrarlo antes de fichar entrada.", "warning")
        # Opcional: Puedes redirigir a un formulario donde el usuario pueda cerrarlo, o incluso cerrarlo automáticamente con la hora actual.
        return redirect(url_for("time.dashboard_employee"))

    try:
        # 1) ¿Tiene hoy un estado NO trabajable?
        today_status = EmployeeStatus.query.filter_by(
            user_id=user_id, date=date.today()
        ).first()
        if today_status and today_status.status in ("Vacaciones", "Baja", "Ausente"):
            flash(
                f"No puedes fichar — tu estado de hoy es «{today_status.status}».",
                "danger"
            )
            return redirect(url_for("time.dashboard_employee"))

        # 2) Bloqueo en Postgres (por si lo usas)
        bind = db.session.get_bind()
        if bind and bind.dialect.name == "postgresql":
            db.session.execute(
                text("LOCK TABLE public.time_record IN SHARE ROW EXCLUSIVE MODE")
            )

        # 3) ¿Ya hay un fichaje abierto?
        existing_open = (
            TimeRecord.query
            .filter_by(user_id=user_id, check_out=None)
            .order_by(desc(TimeRecord.id))
            .first()
        )
        if existing_open:
            flash(
                f"Ya tienes un registro abierto desde "
                f"{existing_open.check_in.strftime('%d-%m-%Y %H:%M:%S')}.",
                "warning"
            )
        else:
            now = datetime.now()

            # --- crear TimeRecord ---
            new_rec = TimeRecord(user_id=user_id, check_in=now, date=now.date())
            db.session.add(new_rec)

            # --- si no existe EmployeeStatus hoy, crearlo como Trabajado ---
            if not today_status:
                user = User.query.get(user_id)
                db.session.add(EmployeeStatus(
                    user_id  = user_id,
                    date     = now.date(),
                    status   = "Trabajado",
                    notes    = "Registro automático de fichaje"
                ))

            db.session.commit()
            flash("Entrada registrada correctamente.", "success")

    except SQLAlchemyError:
        db.session.rollback()
        flash("Error al registrar la entrada. Intenta de nuevo.", "danger")

    return redirect(url_for("time.dashboard_employee"))


# ------------------------------------------------------------------
#  FICHAR SALIDA
# ------------------------------------------------------------------
@time_bp.route("/check_out", methods=["POST"])
def check_out():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    user_id = session["user_id"]

    try:
        bind = db.session.get_bind()
        if bind and bind.dialect.name == "postgresql":
            db.session.execute(
                text("LOCK TABLE public.time_record IN SHARE ROW EXCLUSIVE MODE")
            )

        open_record = (
            TimeRecord.query
            .filter_by(user_id=user_id, check_out=None)
            .order_by(desc(TimeRecord.id))
            .first()
        )
        if open_record:
            now = datetime.now()
            open_record.check_out = now
            open_record.notes = request.form.get("notes", "")
            db.session.commit()
            flash("Salida registrada correctamente.", "success")
        else:
            flash("No tienes ningún fichaje abierto.", "warning")

    except SQLAlchemyError:
        db.session.rollback()
        flash("Error al registrar la salida. Intenta de nuevo.", "danger")

    return redirect(url_for("time.dashboard_employee"))


# ------------------------------------------------------------------
#  DASHBOARD EMPLEADO
# ------------------------------------------------------------------
@time_bp.route("/dashboard")
def dashboard():
    return redirect(url_for("time.dashboard_employee"))


@time_bp.route("/employee/dashboard")
def dashboard_employee():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    user_id = session["user_id"]
    user = User.query.get_or_404(user_id)

    today = date.today()
    start_week = today - timedelta(days=today.weekday())
    end_week   = start_week + timedelta(days=7)

    weekly_records = TimeRecord.query.filter(
        and_(
            TimeRecord.user_id == user_id,
            TimeRecord.date >= start_week,
            TimeRecord.date <  end_week,
            TimeRecord.check_in.isnot(None),
            TimeRecord.check_out.isnot(None)
        )
    ).all()

    worked_secs   = sum((r.check_out - r.check_in).total_seconds() for r in weekly_records)
    allowed_secs  = (user.weekly_hours or 0) * 3600
    remain_secs   = max(allowed_secs - worked_secs, 0)

    recent = (
        TimeRecord.query
        .filter_by(user_id=user_id)
        .order_by(desc(TimeRecord.date), desc(TimeRecord.check_in))
        .limit(3)
        .all()
    )

    recent_fmt = []
    for rec in recent:
        dur = rec.check_out - rec.check_in if rec.check_in and rec.check_out else None
        recent_fmt.append({
            "record": rec,
            "duration_formatted": format_timedelta(dur),
            "remaining": format_timedelta(timedelta(seconds=remain_secs)),
            "is_over": remain_secs == 0
        })

    today_record = (
        TimeRecord.query
        .filter_by(user_id=user_id, date=today, check_out=None)
        .order_by(desc(TimeRecord.id))
        .first()
    )

    return render_template(
        "employee_dashboard.html",
        user=user,
        today_record=today_record,
        recent_records=recent_fmt
    )


# ------------------------------------------------------------------
#  HISTÓRICO INDIVIDUAL
# ------------------------------------------------------------------
@time_bp.route("/history")
def history():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    user_id = session["user_id"]

    recs = (
        TimeRecord.query
        .filter_by(user_id=user_id)
        .order_by(desc(TimeRecord.id))
        .all()
    )
    data = []
    for r in recs:
        dur = r.check_out - r.check_in if r.check_in and r.check_out else None
        data.append({"record": r, "duration_formatted": format_timedelta(dur)})

    return render_template("history.html", records=data)


# ------------------------------------------------------------------
#  CALENDARIO SIMPLE (vista antigua)
# ------------------------------------------------------------------
@time_bp.route("/calendar")
def calendar_view():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    year  = request.args.get("year",  default=date.today().year,  type=int)
    month = request.args.get("month", default=date.today().month, type=int)

    cal = calendar.Calendar()
    month_days = cal.monthdatescalendar(year, month)

    return render_template(
        "calendar.html",
        year=year,
        month=month,
        month_days=month_days
    )

