"""
Backfill retroactivo de fichajes para semanas ya pasadas.

Aplica a datos "antiguos" la misma lógica que el cron semanal:
  1. Cierra los fichajes abiertos (entrada sin salida) a una hora plausible.
  2. Completa los días con fichaje parcial hasta su jornada diaria normal.
  3. Rellena los días vacíos hasta el objetivo semanal (por debajo del contrato,
     con margen variable por empleado y semana).

Es idempotente: solo crea registros en días sin fichaje y solo completa días
con fichaje real, así que puede ejecutarse varias veces sin duplicar nada.

Uso:
    python -m tasks.backfill_range                      # junio y julio del año en curso
    python -m tasks.backfill_range 2026-06-01 2026-07-31
    python -m tasks.backfill_range 2026-06-01 2026-07-31 --dry-run

Solo procesa semanas COMPLETAS (cuyo domingo ya ha pasado); nunca toca la
semana en curso ni días futuros.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta

from tasks.autofill import autofill_week, normalize_week_start
from tasks.scheduler import close_open_record
from models.database import db
from models.models import TimeRecord


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _default_range(today: date) -> tuple[date, date]:
    """Junio y julio del año en curso (hasta hoy)."""
    start = date(today.year, 6, 1)
    end = min(date(today.year, 7, 31), today)
    return start, end


def _week_starts(range_start: date, range_end: date, today: date) -> list[date]:
    """Lunes de cada semana completa que solapa el rango (domingo < hoy)."""
    week = normalize_week_start(range_start)
    last = normalize_week_start(range_end)
    weeks = []
    while week <= last:
        week_end = week + timedelta(days=6)
        if week_end < today:  # semana completa: su domingo ya pasó
            weeks.append(week)
        week += timedelta(days=7)
    return weeks


def _close_open_records_in_week(week_start: date, centro: str | None = None) -> int:
    from models.models import User

    week_end = week_start + timedelta(days=6)
    query = TimeRecord.query.filter(
        TimeRecord.date >= week_start,
        TimeRecord.date <= week_end,
        TimeRecord.check_in.isnot(None),
        TimeRecord.check_out.is_(None),
    )
    if centro:
        query = query.join(User, TimeRecord.user_id == User.id).filter(User.centro == centro)
    open_records = query.all()
    for record in open_records:
        close_open_record(record)
    return len(open_records)


def backfill_range(
    range_start: date,
    range_end: date,
    app=None,
    today: date | None = None,
    dry_run: bool = False,
    centro: str | None = None,
    modified_by: int | None = None,
    verbose: bool = True,
):
    """
    Apply the auto-fill logic to already-completed weeks in a date range.

    Returns a dict with per-week detail and totals so callers (CLI or web) can
    render a summary. Idempotent and safe to run multiple times.
    """
    from tasks.autofill import _get_app  # reutiliza la resolución de app

    app = _get_app(app)
    if app is None:
        raise RuntimeError("Flask app no disponible para backfill_range")

    today = today or date.today()
    weeks = _week_starts(range_start, range_end, today)

    if verbose:
        print(
            f"Backfill de {range_start} a {range_end} "
            f"({'SIMULACIÓN' if dry_run else 'APLICANDO'}) — "
            f"{len(weeks)} semana(s) completa(s)."
        )

    summary = {
        "dry_run": dry_run,
        "weeks": [],
        "closed": 0,
        "created_records": 0,
        "created_seconds": 0,
    }
    for week_start in weeks:
        with app.app_context():
            try:
                closed = _close_open_records_in_week(week_start, centro=centro)
                if not dry_run:
                    db.session.commit()
                else:
                    db.session.flush()

                result = autofill_week(
                    week_start,
                    app=app,
                    centro=centro,
                    modified_by=modified_by,
                    commit=not dry_run,
                )
                skipped = [f"{u.username}: {u.skipped_reason}" for u in result.skipped_users]
            except Exception:
                db.session.rollback()
                raise
            finally:
                if dry_run:
                    db.session.rollback()

        week_info = {
            "week_start": week_start,
            "week_end": week_start + timedelta(days=6),
            "closed": closed,
            "created_records": result.created_records,
            "created_seconds": result.created_seconds,
            "skipped": skipped,
        }
        summary["weeks"].append(week_info)
        summary["closed"] += closed
        summary["created_records"] += result.created_records
        summary["created_seconds"] += result.created_seconds

        if verbose:
            print(
                f"  Semana {week_start} … {week_info['week_end']}: "
                f"{closed} cerrado(s), {result.created_records} creado(s), "
                f"{result.created_seconds / 3600:.1f}h"
                + (f"  | omitidos: {', '.join(skipped)}" if skipped else "")
            )

    if verbose:
        print(
            f"TOTAL: {summary['closed']} fichaje(s) cerrado(s), "
            f"{summary['created_records']} registro(s) creado(s), "
            f"{summary['created_seconds'] / 3600:.1f}h."
        )
    return summary


def main(argv: list[str]) -> None:
    from main import app

    dry_run = "--dry-run" in argv
    positional = [arg for arg in argv if not arg.startswith("-")]

    today = date.today()
    if len(positional) >= 2:
        range_start = _parse_date(positional[0])
        range_end = _parse_date(positional[1])
    else:
        range_start, range_end = _default_range(today)

    backfill_range(range_start, range_end, app=app, today=today, dry_run=dry_run)


if __name__ == "__main__":
    main(sys.argv[1:])
