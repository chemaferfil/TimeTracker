"""
Scheduled tasks for the TimeTracker application.
"""

import os
from datetime import datetime, date, time as dt_time
from zoneinfo import ZoneInfo

from flask import current_app

from models.models import TimeRecord
from models.database import db


AUTO_CLOSE_NOTE = "CA"


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


def _today_in_app_timezone():
    tz_name = os.getenv("APP_TIMEZONE", "Europe/Madrid")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Europe/Madrid")
    return datetime.now(tz).date()


def close_open_record(record: TimeRecord):
    record_date = record.date
    auto_close_time = datetime.combine(record_date, dt_time(23, 59, 59))
    record.check_out = auto_close_time
    record.notes = (record.notes or "") + (" - " if record.notes else "") + AUTO_CLOSE_NOTE
    return auto_close_time


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
            today = _today_in_app_timezone()

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
                    auto_close_time = close_open_record(record)
                    app.logger.info(
                        f"Closed record {record.id} for user {record.user_id} at {auto_close_time}"
                    )

                db.session.commit()
                app.logger.info(f"Successfully auto-closed {len(open_records)} records")
                return len(open_records)
            else:
                app.logger.info("No open records to auto-close")
                return 0

    except Exception as e:
        try:
            app.logger.error(f"Error in auto_close_open_records: {str(e)}")
        except Exception:
            print(f"[auto_close_open_records] Error: {e}")
        if db.session:
            db.session.rollback()
        return 0


def run_scheduled_auto_tasks(app=None):
    """
    Entry point for Render cron: close stale open records daily and auto-fill
    the previous completed week once each Monday in the app timezone.
    """
    app = _get_app(app)
    if app is None:
        raise RuntimeError("Flask app not available for run_scheduled_auto_tasks")

    closed = auto_close_open_records(include_today=False, app=app)
    autofill_result = None

    try:
        with app.app_context():
            today = _today_in_app_timezone()
            if today.weekday() == 0:
                from tasks.autofill import autofill_previous_completed_week

                autofill_result = autofill_previous_completed_week(
                    reference_date=today,
                    app=app,
                )
                app.logger.info(
                    "Weekly autofill completed: %s records created for %s - %s",
                    autofill_result.created_records,
                    autofill_result.week_start,
                    autofill_result.week_end,
                )
            else:
                app.logger.info("Weekly autofill skipped: today is not Monday")
    except Exception as e:
        try:
            app.logger.error(f"Error in weekly autofill: {str(e)}")
        except Exception:
            print(f"[run_scheduled_auto_tasks] Error: {e}")
        if db.session:
            db.session.rollback()

    return {
        "closed": closed,
        "autofill": autofill_result,
    }


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
                    auto_close_time = close_open_record(record)
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
