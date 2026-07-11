"""
Regularización semanal de fichajes.

Objetivo (requisito del cliente): que el total de cada semana refleje ~las horas
de contrato del empleado (15/20/25/30/40), repartidas de forma natural entre los
DÍAS EN QUE EL EMPLEADO REALMENTE TUVO ACTIVIDAD.

Reglas:
  - Los días con fichaje REAL COMPLETO (entrada y salida de verdad, no auto-cierre)
    NO se tocan: son la verdad.
  - Los días "blandos" (entrada sin salida / cerrados a 23:59 «CA» / parciales /
    generados) se recalculan: se conserva la ENTRADA real y se ajusta la SALIDA a
    una duración plausible, de modo que la semana sume ~el contrato.
  - El objetivo semanal es "natural": puede quedar un poco por debajo o por encima
    del contrato (jitter estable por empleado y semana), nunca clavado.
  - Si el empleado tuvo actividad en menos días de lo normal para su jornada, se
    añaden días generados hasta el nº de días típico, para que la media por día sea
    plausible (15h → 3 días de 5h; si vino 5 días → 3h/día).
  - Determinista e idempotente: volver a ejecutarlo deja el mismo estado.

Solo procesa semanas COMPLETAS (nunca la semana en curso ni días futuros).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta

from flask import current_app

from models.database import db
from models.models import EmployeeStatus, OvertimeAlert, TimeRecord, User
from tasks.autofill import (
    AUTO_FILL_RECORD_NOTE,
    BLOCKING_STATUSES,
    LEGACY_AUTO_CLOSE_TIME,
    WEEK_DAYS,
    _generated_start_seconds,
    _get_group_history,
    _has_two_consecutive_days_off,
    _is_employee_active_on_day,
    _record_seconds,
    _stable_minute_offset,
    _stable_signed_offset,
    _target_workday_count,
    _templates_by_weekday,
    _user_history_records,
    normalize_week_start,
)

REG_NOTE = "RG"                     # nota de los fichajes regularizados
REG_REAL_IN_NOTE = "RGE"            # regularizado PERO con entrada real del empleado
REG_SEED = "regularize"
REG_TARGET_JITTER_BP = 500         # ±5% de variación natural del total semanal
REG_DURATION_JITTER_MIN = 8        # ±8 min de variación por día
MIN_DAY_SECONDS = 30 * 60          # no crear días de menos de 30 min
AUTO_CLOSE_NOTE = "CA"
OVERTIME_MARGIN_SECONDS = 3600     # margen para marcar hora extra: > 1h sobre lo esperado
SHORTFALL_MARGIN_SECONDS = 3600    # margen para marcar descuadre: > 1h por debajo del objetivo semanal


def _expected_daily_seconds(user: User) -> int:
    wh = user.weekly_hours or 0
    if wh <= 0:
        return 0
    return int((wh * 3600) / max(_target_workday_count(wh), 1))


def _is_generated(record: TimeRecord) -> bool:
    # REG_NOTE ("RG") también cubre REG_REAL_IN_NOTE ("RGE") por subcadena.
    return AUTO_FILL_RECORD_NOTE in (record.notes or "") or REG_NOTE in (record.notes or "")


def _has_real_check_in(record: TimeRecord) -> bool:
    """
    Entrada fichada de verdad por el empleado: registro no generado, o generado
    en una regularización previa que preservó la entrada real (RGE). Sin esto,
    una segunda regularización trataría la entrada preservada como inventada y
    la movería (bug de idempotencia detectado en la validación del 11/07).
    """
    if not record.check_in:
        return False
    if not _is_generated(record):
        return True
    return REG_REAL_IN_NOTE in (record.notes or "")


def _is_autoclose(record: TimeRecord) -> bool:
    if AUTO_CLOSE_NOTE in (record.notes or ""):
        return True
    return bool(record.check_out and record.check_out.time() == LEGACY_AUTO_CLOSE_TIME)


def _is_solid(record: TimeRecord) -> bool:
    """Fichaje real y completo: entrada y salida de verdad (ni auto-cierre ni generado)."""
    if not record.check_in or not record.check_out:
        return False
    if _is_generated(record) or _is_autoclose(record):
        return False
    return True


@dataclass
class RegUserResult:
    user_id: int
    username: str
    full_name: str
    adjusted_records: int = 0
    removed_records: int = 0
    created_records: int = 0
    overtime_alerts: int = 0
    skipped_reason: str | None = None


@dataclass
class RegResult:
    range_start: date
    range_end: date
    dry_run: bool
    weeks: list[dict] = field(default_factory=list)
    adjusted_records: int = 0
    removed_records: int = 0
    created_records: int = 0
    overtime_alerts: int = 0
    user_results: list[RegUserResult] = field(default_factory=list)


def _get_app(explicit_app=None):
    if explicit_app is not None:
        return explicit_app
    try:
        return current_app._get_current_object()
    except RuntimeError:
        from main import app as main_app
        return main_app


def _week_starts(range_start: date, range_end: date, today: date) -> list[date]:
    """Lunes de cada semana COMPLETA dentro del rango (excluye la semana en curso)."""
    current_week = normalize_week_start(today)
    start = normalize_week_start(range_start)
    weeks = []
    ws = start
    while ws <= range_end:
        if ws + timedelta(days=6) < today and ws < current_week:
            weeks.append(ws)
        ws += timedelta(days=7)
    return weeks


def _weekly_target_seconds(user: User, week_start: date) -> int:
    """Objetivo semanal natural: contrato ± jitter estable (puede quedar por debajo o por encima)."""
    required = int((user.weekly_hours or 0) * 3600)
    if required <= 0:
        return 0
    bp = _stable_signed_offset(user.id, week_start, REG_SEED + ":target", REG_TARGET_JITTER_BP)
    return max(int(required * (10000 + bp) / 10000), 0)


def _detect_overtime(user: User, week_start: date, solid_days: dict[date, int]) -> int:
    """
    Marca como posible hora extra los días con fichaje REAL completo cuya duración
    supera la jornada diaria esperada por encima del margen. Registra/actualiza un
    OvertimeAlert por (empleado, día) conservando el estado 'reviewed'. Devuelve el
    nº de avisos NUEVOS creados.
    """
    expected = _expected_daily_seconds(user)
    if expected <= 0:
        return 0

    new_alerts = 0
    for day, worked in solid_days.items():
        excess = worked - expected
        if excess <= OVERTIME_MARGIN_SECONDS:
            continue
        alert = OvertimeAlert.query.filter_by(user_id=user.id, date=day).first()
        if alert:
            alert.week_start = week_start
            alert.worked_seconds = int(worked)
            alert.expected_seconds = int(expected)
            alert.excess_seconds = int(excess)
        else:
            db.session.add(OvertimeAlert(
                user_id=user.id,
                week_start=week_start,
                date=day,
                worked_seconds=int(worked),
                expected_seconds=int(expected),
                excess_seconds=int(excess),
                reviewed=False,
            ))
            new_alerts += 1
    return new_alerts


def _record_shortfall_alert(user: User, week_start: date, worked: int, target: int) -> int:
    """
    Registra un aviso de DESCUADRE cuando la semana queda por debajo del objetivo
    más allá del margen (p. ej. entrada muy tardía sin salida, cuya jornada se capó
    a las 23:59). Reutiliza la tabla OvertimeAlert con excess_seconds NEGATIVO para
    distinguirlo de una hora extra. Idempotente por (empleado, semana); conserva el
    estado 'reviewed'. Devuelve 1 si el aviso es nuevo, 0 si no.
    """
    shortfall = target - worked
    if shortfall <= SHORTFALL_MARGIN_SECONDS:
        return 0

    existing = OvertimeAlert.query.filter(
        OvertimeAlert.user_id == user.id,
        OvertimeAlert.week_start == week_start,
        OvertimeAlert.excess_seconds < 0,
    ).first()
    if existing:
        existing.worked_seconds = int(worked)
        existing.expected_seconds = int(target)
        existing.excess_seconds = int(worked - target)
        return 0

    # Slot libre en la semana que no choque con un aviso de horas extra (único por empleado+día)
    slot = None
    for i in range(WEEK_DAYS):
        day = week_start + timedelta(days=i)
        if not OvertimeAlert.query.filter_by(user_id=user.id, date=day).first():
            slot = day
            break
    if slot is None:
        return 0

    db.session.add(OvertimeAlert(
        user_id=user.id,
        week_start=week_start,
        date=slot,
        worked_seconds=int(worked),
        expected_seconds=int(target),
        excess_seconds=int(worked - target),
        reviewed=False,
    ))
    return 1


def regularize_range(
    range_start: date,
    range_end: date,
    app=None,
    today: date | None = None,
    dry_run: bool = False,
    centro: str | None = None,
    modified_by: int | None = None,
) -> RegResult:
    app = _get_app(app)
    if app is None:
        raise RuntimeError("Flask app no disponible para regularize_range")

    with app.app_context():
        today = today or date.today()
        result = RegResult(range_start=range_start, range_end=range_end, dry_run=dry_run)

        query = User.query.filter(User.is_admin.is_(False), User.is_active.is_(True))
        if centro:
            query = query.filter(User.centro == centro)
        users = query.order_by(User.full_name.asc(), User.username.asc()).all()

        weeks = _week_starts(range_start, range_end, today)
        pattern_cache: dict = {}

        try:
            for week_start in weeks:
                w_created = w_adjusted = w_removed = w_overtime = 0
                for user in users:
                    ur = _regularize_user_week(user, week_start, modified_by, pattern_cache)
                    if ur.created_records or ur.adjusted_records or ur.removed_records or ur.overtime_alerts:
                        result.user_results.append(ur)
                    w_created += ur.created_records
                    w_adjusted += ur.adjusted_records
                    w_removed += ur.removed_records
                    w_overtime += ur.overtime_alerts
                result.weeks.append({
                    "week_start": week_start,
                    "created_records": w_created,
                    "adjusted_records": w_adjusted,
                    "removed_records": w_removed,
                    "overtime_alerts": w_overtime,
                })
                result.created_records += w_created
                result.adjusted_records += w_adjusted
                result.removed_records += w_removed
                result.overtime_alerts += w_overtime

            if dry_run:
                db.session.rollback()
            else:
                db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        return result


def _regularize_user_week(
    user: User,
    week_start: date,
    modified_by,
    pattern_cache: dict,
) -> RegUserResult:
    ur = RegUserResult(user_id=user.id, username=user.username, full_name=user.full_name)

    required = int((user.weekly_hours or 0) * 3600)
    if required <= 0:
        return ur

    week_days = [week_start + timedelta(days=i) for i in range(WEEK_DAYS)]
    week_end = week_days[-1]
    if user.hire_date and user.hire_date > week_end:
        return ur
    if user.termination_date and user.termination_date < week_start:
        return ur

    records = TimeRecord.query.filter(
        TimeRecord.user_id == user.id,
        TimeRecord.date >= week_start,
        TimeRecord.date <= week_end,
    ).order_by(TimeRecord.date.asc(), TimeRecord.check_in.asc()).all()

    statuses = EmployeeStatus.query.filter(
        EmployeeStatus.user_id == user.id,
        EmployeeStatus.date >= week_start,
        EmployeeStatus.date <= week_end,
    ).all()
    status_by_date = {s.date: s for s in statuses}

    # Clasifica los días de la semana
    recs_by_day: dict[date, list[TimeRecord]] = {}
    for r in records:
        recs_by_day.setdefault(r.date, []).append(r)

    solid_days: dict[date, int] = {}      # día -> segundos reales (se conservan)
    soft_days: dict[date, TimeRecord | None] = {}  # día con actividad blanda -> entrada real a preservar
    for day, day_recs in recs_by_day.items():
        if any(_is_solid(r) for r in day_recs):
            solid_days[day] = sum(_record_seconds(r) for r in day_recs if _is_solid(r))
        else:
            # Día "blando": conserva la entrada real más temprana (si la hay),
            # incluida la preservada en regularizaciones anteriores (RGE)
            real_ins = [r for r in day_recs if _has_real_check_in(r)]
            keep_in = min(real_ins, key=lambda r: r.check_in) if real_ins else None
            soft_days[day] = keep_in

    # Detección de horas extra: días REALES completos que superan la jornada
    # esperada por encima del margen. Se registra para avisar al administrador.
    ur.overtime_alerts += _detect_overtime(user, week_start, solid_days)

    solid_seconds = sum(solid_days.values())
    target_seconds = _weekly_target_seconds(user, week_start)
    remaining = max(target_seconds - solid_seconds, 0)

    # Días candidatos a llevar horas (los blandos donde el empleado vino), ordenados
    soft_presence = sorted(
        d for d in soft_days
        if _is_employee_active_on_day(user, d)
        and not (status_by_date.get(d) and status_by_date[d].status in BLOCKING_STATUSES)
    )

    # Nº de días objetivo: al menos los que vino; si son pocos, completa hasta el típico
    typical = max(_target_workday_count(user.weekly_hours or 0) - len(solid_days), 1)
    target_days = list(soft_presence)
    if len(target_days) < typical and remaining > 0:
        target_days = _extend_with_generated_days(
            user, week_days, set(solid_days) | set(target_days), status_by_date, typical - len(target_days)
        )
        target_days = sorted(set(soft_presence) | set(target_days))

    # Borra los registros blandos (los recrearemos de forma coherente)
    for day, day_recs in recs_by_day.items():
        if day in solid_days:
            continue
        for r in day_recs:
            db.session.delete(r)
            ur.removed_records += 1

    if remaining <= 0 or not target_days:
        # Las horas reales ya cubren el objetivo (o no vino): solo se limpiaron los blandos.
        for day in list(soft_days):
            _sync_status_after_clear(status_by_date, day, solid_days)
        return ur

    # Reparte "remaining" entre los días objetivo, con jitter natural por día
    n = len(target_days)
    base = remaining // n
    templates = _templates_by_weekday(_user_history_records(user.id, week_start), "histórico empleado")
    group_templates, _ = _get_group_history(user, week_start, pattern_cache)

    created_seconds = 0
    for day in target_days:
        weekday = day.weekday()
        dur = base + 60 * _stable_signed_offset(user.id, day, REG_SEED + ":dur", REG_DURATION_JITTER_MIN)
        dur = max(_round_min(dur), MIN_DAY_SECONDS)

        # Entrada: la real si la hay (día blando con entrada), si no una plausible del patrón
        keep_in = soft_days.get(day)
        has_real_in = bool(keep_in and keep_in.check_in)
        if has_real_in:
            check_in = keep_in.check_in
        else:
            start_seconds = _start_seconds_for(user, weekday, templates, group_templates)
            shift = timedelta(minutes=_stable_minute_offset(user.id, day, REG_SEED))
            check_in = datetime.combine(day, dt_time(0, 0)) + timedelta(seconds=start_seconds) + shift

        check_out = check_in + timedelta(seconds=dur)
        max_end = datetime.combine(day, dt_time(23, 59, 59))
        if check_out > max_end:
            check_out = max_end
            # Una entrada REAL no se mueve: el día queda corto y se avisará del
            # descuadre. Solo las entradas generadas se adelantan para cuadrar.
            if not has_real_in:
                check_in = max(check_out - timedelta(seconds=dur), datetime.combine(day, dt_time(0, 0)))
        if check_out <= check_in:
            continue

        db.session.add(TimeRecord(
            user_id=user.id, check_in=check_in, check_out=check_out, date=day,
            notes=REG_REAL_IN_NOTE if has_real_in else REG_NOTE,
            modified_by=modified_by,
        ))
        ur.created_records += 1
        created_seconds += int((check_out - check_in).total_seconds())

        st = status_by_date.get(day)
        if st:
            st.status = "Trabajado"
            st.entry_time = check_in.time()
            st.exit_time = check_out.time()
        else:
            new_st = EmployeeStatus(
                user_id=user.id, date=day, status="Trabajado",
                entry_time=check_in.time(), exit_time=check_out.time(), notes=REG_NOTE,
            )
            db.session.add(new_st)
            status_by_date[day] = new_st

    # Descuadre: la semana queda claramente por debajo del objetivo (p. ej. una
    # entrada muy tardía sin salida capada a las 23:59) → aviso para el admin.
    ur.overtime_alerts += _record_shortfall_alert(
        user, week_start, solid_seconds + created_seconds, target_seconds
    )

    return ur


def _extend_with_generated_days(
    user: User,
    week_days: list[date],
    taken: set[date],
    status_by_date: dict,
    need: int,
) -> list[date]:
    """Elige días extra plausibles respetando 2 días seguidos libres y ausencias."""
    added: list[date] = []
    for day in week_days:
        if need <= 0:
            break
        if day in taken or day in added:
            continue
        if not _is_employee_active_on_day(user, day):
            continue
        st = status_by_date.get(day)
        if st and st.status in BLOCKING_STATUSES:
            continue
        candidate = taken | set(added) | {day}
        if not _has_two_consecutive_days_off(candidate, week_days):
            continue
        added.append(day)
        need -= 1
    return added


def _start_seconds_for(user, weekday, templates, group_templates) -> int:
    tpl = templates.get(weekday) or group_templates.get(weekday)
    if tpl:
        return tpl.start_seconds
    return _generated_start_seconds(user)


def _sync_status_after_clear(status_by_date, day, solid_days):
    """Si un día blando se queda sin fichaje, no dejamos un estado 'Trabajado' generado colgando."""
    st = status_by_date.get(day)
    if st and (st.notes or "").strip() in (REG_NOTE, AUTO_FILL_RECORD_NOTE) and day not in solid_days:
        st.entry_time = None
        st.exit_time = None


def _round_min(seconds: int) -> int:
    return (int(seconds) // 60) * 60
