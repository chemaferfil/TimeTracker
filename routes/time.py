from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from sqlalchemy import desc, text
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime
from models.models import User, TimeRecord
from models.database import db

# Blueprint para rutas de fichaje
time_bp = Blueprint("time", __name__, template_folder="templates")

# Función auxiliar para dar formato al tiempo transcurrido
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
        db.session.execute(text("LOCK TABLE public.time_record IN SHARE ROW EXCLUSIVE MODE"))

        existing_open = (
            TimeRecord.query
                .filter_by(user_id=user_id, check_out=None)
                .order_by(desc(TimeRecord.id))
                .first()
        )
        if existing_open:
            flash(
                f"Ya tienes un registro de entrada abierto desde {existing_open.check_in.strftime('%d-%m-%Y %H:%M:%S')}. Debes fichar la salida primero.",
                "warning"
            )
        else:
            now = datetime.now()
            new_record = TimeRecord(
                user_id=user_id,
                check_in=now,
                date=now.date()
            )
            db.session.add(new_record)
            db.session.commit()
            flash("Entrada registrada correctamente.", "success")

            try:
                socketio = current_app.extensions['socketio']
                socketio.emit(
                    'user_checked_in',
                    {
                        'user_id': user_id,
                        'check_in': new_record.check_in.isoformat()
                    })
            except Exception as e:
                current_app.logger.error(f"Error al emitir user_checked_in: {e}")
    except SQLAlchemyError:
        db.session.rollback()
        flash("Error al registrar la entrada. Intenta de nuevo.", "danger")

    return redirect(url_for("auth.dashboard"))

@time_bp.route("/check_out", methods=["POST"])
def check_out():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    user_id = session["user_id"]

    try:
        db.session.execute(text("LOCK TABLE public.time_record IN SHARE ROW EXCLUSIVE MODE"))

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

            try:
                socketio = current_app.extensions['socketio']
                socketio.emit(
                    'user_checked_out',
                    {
                        'user_id': user_id,
                        'check_out': open_record.check_out.isoformat()
                    })
            except Exception as e:
                current_app.logger.error(f"Error al emitir user_checked_out: {e}")
        else:
            flash("No tienes ninguna entrada abierta.", "warning")
    except SQLAlchemyError:
        db.session.rollback()
        flash("Error al registrar la salida. Intenta de nuevo.", "danger")

    return redirect(url_for("auth.dashboard"))

@time_bp.route("/dashboard")
def dashboard():
    return redirect(url_for("auth.dashboard"))

@time_bp.route("/history")
def history():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    user_id = session["user_id"]

    records = (
        TimeRecord.query
            .filter_by(user_id=user_id)
            .order_by(desc(TimeRecord.id))
            .all()
    )

    records_with_duration = []
    for record in records:
        duration = None
        if record.check_in and record.check_out:
            duration = record.check_out - record.check_in
        records_with_duration.append({
            "record": record,
            "duration_formatted": format_timedelta(duration)
        })

    return render_template("history.html", records=records_with_duration)
