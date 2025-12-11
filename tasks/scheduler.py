"""
Scheduled tasks for the TimeTracker application.
"""

from datetime import datetime, date, time as dt_time
from flask import current_app

from models.models import TimeRecord
from models.database import db


AUTO_CLOSE_NOTE = "Cerrado automáticamente"


def _get_app(explicit_app=None):
    """Return a Flask app instance whether we're inside a request/context or not."""
    if explicit_app is not None:
        return explicit_app
    try:
        return current_app._get_current_object()
    except RuntimeError:
        # Lazy import to avoid circular imports at module load.
        try:
            from main import app as main_app
            return main_app
        except Exception:
            return None


def auto_close_open_records(include_today: bool = True, app=None):
    """
    Auto-close open time records at 23:59:59 of their respective dates.
    Intended to be called daily by the scheduler or by an external cron job.

    Args:
        include_today: If False, only close records with date < today.
                       Useful when the job runs after midnight to avoid
                       closing the new day's records.
        app: Optional Flask app to use when running outside an app context.
    """
    app = _get_app(app)
    if app is None:
        raise RuntimeError("Flask app not available for auto_close_open_records")

    try:
        with app.app_context():
            today = date.today()

            query = TimeRecord.query.filter(
                TimeRecord.check_in.isnot(None),
                TimeRecord.check_out.is_(None)
            )
            if not include_today:
                query = query.filter(TimeRecord.date < today)

            open_records = query.all()

            if open_records:
                app.logger.info(f"Auto-closing {len(open_records)} open time records")

                for record in open_records:
                    record_date = record.date
                    auto_close_time = datetime.combine(record_date, dt_time(23, 59, 59))
                    record.check_out = auto_close_time
                    record.notes = (record.notes or "") + (" - " if record.notes else "") + AUTO_CLOSE_NOTE
                    app.logger.info(
                        f"Closed record {record.id} for user {record.user_id} at {auto_close_time}"
                    )

                db.session.commit()
                app.logger.info(f"Successfully auto-closed {len(open_records)} records")
            else:
                app.logger.info("No open records to auto-close")

    except Exception as e:
        try:
            app.logger.error(f"Error in auto_close_open_records: {str(e)}")
        except Exception:
            print(f"[auto_close_open_records] Error: {e}")
        if db.session:
            db.session.rollback()


def manual_auto_close_records(target_date=None, app=None):
    """
    Manual function to close open records.
    Used for testing or administrative purposes.

    Args:
        target_date: If specified, only close records for that date.
                     If None, close ALL open records (recommended).
        app: Optional Flask app to use when running outside an app context.
    """
    app = _get_app(app)
    if app is None:
        raise RuntimeError("Flask app not available for manual_auto_close_records")

    try:
        with app.app_context():
            if target_date is None:
                open_records = TimeRecord.query.filter(
                    TimeRecord.check_in.isnot(None),
                    TimeRecord.check_out.is_(None)
                ).all()
                app.logger.info(f"Manual auto-closing ALL {len(open_records)} open time records")
            else:
                open_records = TimeRecord.query.filter(
                    TimeRecord.date == target_date,
                    TimeRecord.check_in.isnot(None),
                    TimeRecord.check_out.is_(None)
                ).all()
                app.logger.info(
                    f"Manual auto-closing {len(open_records)} open time records for {target_date}"
                )

            if open_records:
                for record in open_records:
                    record_date = record.date
                    auto_close_time = datetime.combine(record_date, dt_time(23, 59, 59))
                    record.check_out = auto_close_time
                    record.notes = (record.notes or "") + (" - " if record.notes else "") + AUTO_CLOSE_NOTE
                    app.logger.info(
                        f"Closed record {record.id} for user {record.user_id} at {auto_close_time}"
                    )

                db.session.commit()
                return len(open_records)

            app.logger.info("No open records to auto-close")
            return 0

    except Exception as e:
        try:
            app.logger.error(f"Error in manual_auto_close_records: {str(e)}")
        except Exception:
            print(f"[manual_auto_close_records] Error: {e}")
        if db.session:
            db.session.rollback()
        raise
