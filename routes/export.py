from flask import Blueprint, render_template, request, redirect, url_for, flash, session, send_file
from functools import wraps
from models.models import User, TimeRecord
from models.database import db
from werkzeug.security import generate_password_hash
from datetime import datetime, timedelta
import os
import tempfile
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter

export_bp = Blueprint("export", __name__, template_folder="../templates")

# Decorator to check if user is admin
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Acceso no autorizado. Se requieren permisos de administrador.", "danger")
            return redirect(url_for("auth.login"))
        # Check if user still exists and is admin in DB for extra security
        user = User.query.get(session.get("user_id"))
        if not user or not user.is_admin:
            session.clear()
            flash("Tu cuenta ya no tiene permisos de administrador.", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function

@export_bp.route("/excel", methods=["GET", "POST"])
@admin_required
def export_excel():
    if request.method == "POST":
        # Get filter parameters
        start_date = request.form.get("start_date")
        end_date = request.form.get("end_date")
        user_id = request.form.get("user_id")
        
        # Validate dates
        try:
            if start_date:
                start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
            else:
                # Default to first day of current month if not specified
                today = datetime.now().date()
                start_date = datetime(today.year, today.month, 1).date()
                
            if end_date:
                end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
            else:
                # Default to today if not specified
                end_date = datetime.now().date()
                
            # Ensure end_date is not before start_date
            if end_date < start_date:
                flash("La fecha de fin no puede ser anterior a la fecha de inicio.", "danger")
                return redirect(url_for("export.export_excel"))
                
        except ValueError:
            flash("Formato de fecha inválido. Use YYYY-MM-DD.", "danger")
            return redirect(url_for("export.export_excel"))
        
        # Build query based on filters
        query = TimeRecord.query.filter(TimeRecord.date >= start_date, TimeRecord.date <= end_date)
        
        if user_id and user_id != "all":
            query = query.filter(TimeRecord.user_id == user_id)
            
        # Order by user and date
        records = query.order_by(TimeRecord.user_id, TimeRecord.date).all()
        
        if not records:
            flash("No hay registros para el período seleccionado.", "warning")
            return redirect(url_for("export.export_excel"))
            
        # Generate Excel file
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Registros de Fichaje"
        
        # Add header
        header = ["Usuario", "Fecha", "Entrada", "Salida", "Horas Trabajadas", "Notas", "Modificado Por", "Última Actualización"]
        for col_num, header_text in enumerate(header, 1):
            cell = ws.cell(row=1, column=col_num)
            cell.value = header_text
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal='center')
            cell.fill = PatternFill(start_color="DDDDDD", end_color="DDDDDD", fill_type="solid")
            
        # Add data
        row_num = 2
        for record in records:
            user = User.query.get(record.user_id)
            modified_by = User.query.get(record.modified_by) if record.modified_by else None
            
            # Calculate hours worked if both check_in and check_out exist
            hours_worked = ""
            if record.check_in and record.check_out:
                time_diff = record.check_out - record.check_in
                hours = time_diff.total_seconds() / 3600
                hours_worked = f"{hours:.2f}"
            
            ws.cell(row=row_num, column=1).value = user.username if user else f"Usuario ID: {record.user_id}"
            ws.cell(row=row_num, column=2).value = record.date.strftime("%Y-%m-%d")
            ws.cell(row=row_num, column=3).value = record.check_in.strftime("%H:%M:%S") if record.check_in else "-"
            ws.cell(row=row_num, column=4).value = record.check_out.strftime("%H:%M:%S") if record.check_out else "-"
            ws.cell(row=row_num, column=5).value = hours_worked
            ws.cell(row=row_num, column=6).value = record.notes
            ws.cell(row=row_num, column=7).value = modified_by.username if modified_by else "-"
            ws.cell(row=row_num, column=8).value = record.updated_at.strftime("%Y-%m-%d %H:%M:%S")
            
            row_num += 1
            
        # Auto-adjust column widths
        for col_num, _ in enumerate(header, 1):
            col_letter = get_column_letter(col_num)
            ws.column_dimensions[col_letter].width = 15
            
        # Save to temporary file
        fd, temp_path = tempfile.mkstemp(suffix='.xlsx')
        os.close(fd)
        wb.save(temp_path)
        
        # Generate filename based on date range
        filename = f"registros_{start_date.strftime('%Y%m%d')}_a_{end_date.strftime('%Y%m%d')}.xlsx"
        
        return send_file(
            temp_path,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        
    # GET request - show export form
    users = User.query.filter_by(is_active=True).order_by(User.username).all()
    return render_template("export_excel.html", users=users)
