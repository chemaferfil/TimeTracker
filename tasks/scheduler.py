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
    """
    Close an open record at a plausible check-out based on the employee's
    typical shift duration (own history, group history or contracted hours).

    Ya NO se usa el cierre a las 23:59:59: generaba jornadas ficticias enormes
    (entrada sin salida) que descuadraban las semanas. Si no se puede estimar
    una salida plausible (p. ej. empleado sin jornada contratada), el registro
    se deja ABIERTO y lo resolverá la regularización semanal del lunes.
    """
    from tasks.autofill import estimate_auto_close_time

    try:
        auto_close_time = estimate_auto_close_time(record)
    except Exception:
        auto_close_time = None
    if auto_close_time is None:
        # Sin base para estimar: no inventamos una salida (nada de 23:59).
        return None

    record.check_out = auto_close_time
    record.notes = (record.notes or "") + (" - " if record.notes else "") + AUTO_CLOSE_NOTE
    return auto_close_time


def auto_close_open_records(include_today: bool = True, app=None):
    """
    Auto-close open time records at a plausible check-out time based on each
    employee's usual shift (fallback: 23:59:59 of the record date).
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

                closed = 0
                for record in open_records:
                    auto_close_time = close_open_record(record)
                    if auto_close_time is None:
                        # No se pudo estimar: se deja abierto para la regularización.
                        continue
                    closed += 1
                    app.logger.info(
                        f"Closed record {record.id} for user {record.user_id} at {auto_close_time}"
                    )

                db.session.commit()
                app.logger.info(f"Successfully auto-closed {closed} of {len(open_records)} records")
                return closed
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
    Entry point for Render cron: close stale open records daily and, cada lunes,
    REGULARIZA la semana recién cerrada (reparte las horas de contrato entre los
    días con actividad, corrige inflados y detecta horas extra para el admin).
    """
    app = _get_app(app)
    if app is None:
        raise RuntimeError("Flask app not available for run_scheduled_auto_tasks")

    closed = auto_close_open_records(include_today=False, app=app)
    regularize_result = None

    try:
        with app.app_context():
            today = _today_in_app_timezone()
            if today.weekday() == 0:
                from datetime import timedelta
                from tasks.regularize import regularize_range

                prev_week_start = _monday_of(today) - timedelta(days=7)
                prev_week_end = prev_week_start + timedelta(days=6)
                regularize_result = regularize_range(
                    prev_week_start,
                    prev_week_end,
                    app=app,
                    today=today,
                    dry_run=False,
                )
                app.logger.info(
                    "Weekly regularization done for %s - %s: %s creados, %s quitados, "
                    "%s avisos de horas extra",
                    prev_week_start,
                    prev_week_end,
                    regularize_result.created_records,
                    regularize_result.removed_records,
                    regularize_result.overtime_alerts,
                )
            else:
                app.logger.info("Weekly regularization skipped: today is not Monday")
    except Exception as e:
        try:
            app.logger.error(f"Error in weekly regularization: {str(e)}")
        except Exception:
            print(f"[run_scheduled_auto_tasks] Error: {e}")
        if db.session:
            db.session.rollback()

    return {
        "closed": closed,
        "regularize": regularize_result,
    }


def _monday_of(day):
    from datetime import timedelta
    return day - timedelta(days=day.weekday())


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
