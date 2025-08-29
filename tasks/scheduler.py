"""
Scheduled tasks for the TimeTracker application.
"""
from datetime import datetime, date, time as dt_time
from models.models import TimeRecord
from models.database import db
from flask import current_app


def auto_close_open_records():
    """
    Auto-close all open time records at 23:59:59.
    This function is called by the scheduler daily.
    """
    try:
        with current_app.app_context():
            # Get current date and create the auto-close datetime (23:59:59 of the same day)
            today = date.today()
            auto_close_time = datetime.combine(today, dt_time(23, 59, 59))
            
            # Find all open records from today (check_in not null, check_out is null)
            open_records = TimeRecord.query.filter(
                TimeRecord.date == today,
                TimeRecord.check_in.isnot(None),
                TimeRecord.check_out.is_(None)
            ).all()
            
            if open_records:
                current_app.logger.info(f"Auto-closing {len(open_records)} open time records for {today}")
                
                # Close all open records
                for record in open_records:
                    record.check_out = auto_close_time
                    record.notes = (record.notes or "") + (" - " if record.notes else "") + "Cerrado automáticamente"
                
                # Commit all changes
                db.session.commit()
                current_app.logger.info(f"Successfully auto-closed {len(open_records)} records")
            else:
                current_app.logger.info(f"No open records to auto-close for {today}")
                
    except Exception as e:
        current_app.logger.error(f"Error in auto_close_open_records: {str(e)}")
        if db.session:
            db.session.rollback()


def manual_auto_close_records(target_date=None):
    """
    Manual function to close open records for a specific date.
    Used for testing or administrative purposes.
    
    Args:
        target_date: The date to close records for (defaults to today)
    """
    try:
        with current_app.app_context():
            if target_date is None:
                target_date = date.today()
            
            auto_close_time = datetime.combine(target_date, dt_time(23, 59, 59))
            
            # Find all open records from the target date
            open_records = TimeRecord.query.filter(
                TimeRecord.date == target_date,
                TimeRecord.check_in.isnot(None),
                TimeRecord.check_out.is_(None)
            ).all()
            
            if open_records:
                current_app.logger.info(f"Manual auto-closing {len(open_records)} open time records for {target_date}")
                
                # Close all open records
                for record in open_records:
                    record.check_out = auto_close_time
                    record.notes = (record.notes or "") + (" - " if record.notes else "") + "Cerrado automáticamente"
                
                # Commit all changes
                db.session.commit()
                return len(open_records)
            else:
                current_app.logger.info(f"No open records to auto-close for {target_date}")
                return 0
                
    except Exception as e:
        current_app.logger.error(f"Error in manual_auto_close_records: {str(e)}")
        if db.session:
            db.session.rollback()
        raise