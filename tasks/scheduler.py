"""
Scheduled tasks for the TimeTracker application.
"""
from datetime import datetime, date, time as dt_time
from models.models import TimeRecord
from models.database import db
from flask import current_app


def auto_close_open_records():
    """
    Auto-close all open time records at 23:59:59 of their respective dates.
    This function is called by the scheduler daily.

    Important: This closes ALL open records (not just today's) to handle:
    - Records from previous days that were never closed
    - Edge cases where the scheduler runs just after midnight
    """
    try:
        with current_app.app_context():
            # Find ALL open records (check_in not null, check_out is null)
            # No date filter - we want to close any open record regardless of date
            open_records = TimeRecord.query.filter(
                TimeRecord.check_in.isnot(None),
                TimeRecord.check_out.is_(None)
            ).all()

            if open_records:
                current_app.logger.info(f"Auto-closing {len(open_records)} open time records")

                # Close all open records at 23:59:59 of THEIR respective dates
                for record in open_records:
                    # Use the record's own date to set 23:59:59 of that day
                    record_date = record.date
                    auto_close_time = datetime.combine(record_date, dt_time(23, 59, 59))
                    record.check_out = auto_close_time
                    record.notes = (record.notes or "") + (" - " if record.notes else "") + "Cerrado automáticamente"
                    current_app.logger.info(f"Closed record {record.id} for user {record.user_id} at {auto_close_time}")

                # Commit all changes
                db.session.commit()
                current_app.logger.info(f"Successfully auto-closed {len(open_records)} records")
            else:
                current_app.logger.info("No open records to auto-close")
                
    except Exception as e:
        current_app.logger.error(f"Error in auto_close_open_records: {str(e)}")
        if db.session:
            db.session.rollback()


def manual_auto_close_records(target_date=None):
    """
    Manual function to close open records.
    Used for testing or administrative purposes.

    Args:
        target_date: If specified, only close records for that date.
                     If None, close ALL open records (recommended).
    """
    try:
        with current_app.app_context():
            if target_date is None:
                # Close ALL open records regardless of date
                open_records = TimeRecord.query.filter(
                    TimeRecord.check_in.isnot(None),
                    TimeRecord.check_out.is_(None)
                ).all()
                current_app.logger.info(f"Manual auto-closing ALL {len(open_records)} open time records")
            else:
                # Close only records for the specific date
                open_records = TimeRecord.query.filter(
                    TimeRecord.date == target_date,
                    TimeRecord.check_in.isnot(None),
                    TimeRecord.check_out.is_(None)
                ).all()
                current_app.logger.info(f"Manual auto-closing {len(open_records)} open time records for {target_date}")

            if open_records:
                # Close all open records at 23:59:59 of THEIR respective dates
                for record in open_records:
                    record_date = record.date
                    auto_close_time = datetime.combine(record_date, dt_time(23, 59, 59))
                    record.check_out = auto_close_time
                    record.notes = (record.notes or "") + (" - " if record.notes else "") + "Cerrado automáticamente"
                    current_app.logger.info(f"Closed record {record.id} for user {record.user_id} at {auto_close_time}")

                # Commit all changes
                db.session.commit()
                return len(open_records)
            else:
                current_app.logger.info("No open records to auto-close")
                return 0

    except Exception as e:
        current_app.logger.error(f"Error in manual_auto_close_records: {str(e)}")
        if db.session:
            db.session.rollback()
        raise