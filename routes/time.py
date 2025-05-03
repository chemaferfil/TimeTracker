from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from sqlalchemy import desc
from datetime import datetime, date, timedelta
from models.models import User, TimeRecord
from models.database import db

time_bp = Blueprint("time", __name__, template_folder="../templates")

@time_bp.route("/check_in", methods=["POST"])
def check_in():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    
    user_id = session["user_id"]
    
    # Check if there is ANY open record for this user, regardless of date
    existing_open_record = TimeRecord.query.filter_by(
        user_id=user_id, 
        check_out=None
    ).order_by(desc(TimeRecord.id)).first()
    
    if existing_open_record:
        flash(f"Ya tienes un registro de entrada abierto desde {existing_open_record.check_in.strftime('%d-%m-%Y %H:%M:%S')}. Debes fichar la salida primero.", "warning")
    else:
        # Create a new record for today
        new_record = TimeRecord(
            user_id=user_id,
            check_in=datetime.now(),
            date=date.today() # Record the date the check-in occurred
        )
        db.session.add(new_record)
        db.session.commit()
        flash("Entrada registrada correctamente.", "success")
    
    return redirect(url_for("time.dashboard"))

@time_bp.route("/check_out", methods=["POST"])
def check_out():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    
    user_id = session["user_id"]
    
    # Find the most recent open record for this user, regardless of date
    open_record = TimeRecord.query.filter_by(
        user_id=user_id, 
        check_out=None
    ).order_by(desc(TimeRecord.id)).first()
    
    if open_record:
        open_record.check_out = datetime.now()
        # Get notes from the form
        notes = request.form.get("notes", "") # Get notes, default to empty string if not provided
        open_record.notes = notes # Save notes to the record
        
        # Update the date field to the date of check-out if it spans multiple days? 
        # Or keep the original check-in date? Let's keep original for now.
        # open_record.date = open_record.check_out.date() 
        
        db.session.commit()
        flash("Salida registrada correctamente.", "success")
    else:
        # This case should ideally not happen if UI prevents check-out when not checked-in
        flash("No tienes un registro de entrada abierto para fichar la salida.", "danger")
    
    return redirect(url_for("time.dashboard"))

@time_bp.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    
    user_id = session["user_id"]
    user = User.query.get(user_id)
    
    # Find the current open record for the user, if any
    current_open_record = (
        TimeRecord.query
            .filter_by(user_id=user_id, check_out=None)
            .order_by(desc(TimeRecord.id))
            .first()
    )
    
    # Recent records for history (most recent first)
    recent_records = (
        TimeRecord.query
            .filter_by(user_id=user_id)
            .order_by(desc(TimeRecord.id)) # Sort by ID descending
            .limit(7)
            .all()
    )
    
    # Helper function to format timedelta
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
    
    return render_template(
        "employee_dashboard.html",
        user=user,
        today_record=current_open_record, 
        recent_records=recent_records_with_duration, # Pass records with duration
        current_date=date.today()
    )

@time_bp.route("/history")
def history():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    
    user_id = session["user_id"]
    
    # All records for the user (most recent first)
    records = (
        TimeRecord.query
            .filter_by(user_id=user_id)
            .order_by(desc(TimeRecord.id)) # Sort by ID descending
            .all()
    )
    
    # Helper function to format timedelta (reuse from dashboard)
    def format_timedelta(td):
        if td is None:
            return "-"
        total_seconds = int(td.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    # Calculate worked time for all records
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


