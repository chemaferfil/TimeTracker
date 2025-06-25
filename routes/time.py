from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from sqlalchemy import desc, text, and_
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, date, timedelta
from models.models import TimeRecord, User
from models.database import db

time_bp = Blueprint("time", __name__)

def format_timedelta(td):
    if td is None:
        return "-"
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

@time_bp.route("/check_in", methods=["POST"])
def check_in():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    user_id = session["user_id"]

    try:
        bind = db.session.get_bind()
        if bind and bind.dialect.name == "postgresql":
            db.session.execute(text("LOCK TABLE public.time_record IN SHARE ROW EXCLUSIVE MODE"))

        existing_open = TimeRecord.query.filter_by(user_id=user_id, check_out=None).order_by(desc(TimeRecord.id)).first()
        if existing_open:
            flash(f"Ya tienes un registro abierto desde {existing_open.check_in.strftime('%d-%m-%Y %H:%M:%S')}.", "warning")
        else:
            now = datetime.now()
            new_record = TimeRecord(user_id=user_id, check_in=now, date=now.date())
            db.session.add(new_record)
            db.session.commit()
            flash("Entrada registrada correctamente.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        flash("Error al registrar la entrada. Intenta de nuevo.", "danger")

    return redirect(url_for("time.dashboard_employee"))

@time_bp.route("/check_out", methods=["POST"])
def check_out():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    user_id = session["user_id"]

    try:
        bind = db.session.get_bind()
        if bind and bind.dialect.name == "postgresql":
            db.session.execute(text("LOCK TABLE public.time_record IN SHARE ROW EXCLUSIVE MODE"))

        open_record = TimeRecord.query.filter_by(user_id=user_id, check_out=None).order_by(desc(TimeRecord.id)).first()
        if open_record:
            now = datetime.now()
            open_record.check_out = now
            open_record.notes = request.form.get("notes", "")
            db.session.commit()
            flash("Salida registrada correctamente.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        flash("Error al registrar la salida. Intenta de nuevo.", "danger")

    return redirect(url_for("time.dashboard_employee"))

@time_bp.route("/dashboard")
def dashboard():
    return redirect(url_for("time.dashboard_employee"))

@time_bp.route("/employee/dashboard")
def dashboard_employee():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    user_id = session["user_id"]
    user = User.query.get_or_404(user_id)

    # Calcular tiempo trabajado esta semana
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=7)

    weekly_records = TimeRecord.query.filter(
        and_(
            TimeRecord.user_id == user_id,
            TimeRecord.date >= start_of_week,
            TimeRecord.date < end_of_week,
            TimeRecord.check_in.isnot(None),
            TimeRecord.check_out.isnot(None)
        )
    ).all()

    total_worked = sum((r.check_out - r.check_in).total_seconds() for r in weekly_records)
    total_allowed = (user.weekly_hours or 0) * 3600
    remaining_seconds = max(total_allowed - total_worked, 0)
    remaining_formatted = format_timedelta(timedelta(seconds=remaining_seconds))

    # Últimos registros
    recent = TimeRecord.query.filter_by(user_id=user_id).order_by(desc(TimeRecord.date), desc(TimeRecord.check_in)).limit(3).all()

    recent_records = []
    for rec in recent:
      duration = rec.check_out - rec.check_in if rec.check_in and rec.check_out else None
      remaining_to_display = remaining_formatted if rec.check_in and rec.check_out else "-"
      recent_records.append({
         "record": rec,
         "duration_formatted": format_timedelta(duration),
         "remaining": format_timedelta(timedelta(seconds=remaining_seconds)),
         "is_over": remaining_seconds <= 0
       })

    # Registro abierto de hoy
    today_record = TimeRecord.query.filter_by(user_id=user_id, date=today, check_out=None).order_by(desc(TimeRecord.id)).first()

    return render_template(
        "employee_dashboard.html",
        user=user,
        today_record=today_record,
        recent_records=recent_records
    )

@time_bp.route("/history")
def history():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    user_id = session["user_id"]

    records = TimeRecord.query.filter_by(user_id=user_id).order_by(desc(TimeRecord.id)).all()
    records_with_duration = []
    for record in records:
        duration = record.check_out - record.check_in if record.check_in and record.check_out else None
        records_with_duration.append({
            "record": record,
            "duration_formatted": format_timedelta(duration)
        })

    return render_template("history.html", records=records_with_duration)
