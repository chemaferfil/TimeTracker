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
    WEEK_DAYS,
    _generated_start_seconds,
    _get_group_history,
    _has_two_consecutive_days_off,
    _is_employee_active_on_day,
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
REG_TARGET_JITTER_MIN = 12         # ±12 min de variación del total semanal (mínima, no %)
REG_DURATION_JITTER_MIN = 8        # ±8 min de variación por día
MIN_DAY_SECONDS = 30 * 60          # no crear días de menos de 30 min
MAX_REG_WORKDAYS = 5               # tope de días laborables generados (2 libres seguidos)


def _max_daily_seconds(user: User) -> int:
    """
    Tope diario de horas por contrato. Tabla acordada con el cliente (jul-2026):
    NINGÚN día (real o generado) puede superar este máximo. Si un fichaje real
    excede el tope, la regularización lo recorta; no se muestran horas extra
    (se gestionan en otra aplicación).
    Diseño: tope × MAX_REG_WORKDAYS ≥ jornada semanal, así el contrato cuadra.

        ≤5h→2h · 7-10h→3h · 12h→4h · 15-20h→5h · 25h→6h · 30h→7h · ≥40h→8h
    """
    wh = user.weekly_hours or 0
    if wh <= 0:
        return 0
    if wh <= 5:
        cap_h = 2
    elif wh <= 10:
        cap_h = 3
    elif wh <= 12:
        cap_h = 4
    elif wh <= 20:
        cap_h = 5
    elif wh <= 25:
        cap_h = 6
    elif wh <= 30:
        cap_h = 7
    else:
        cap_h = 8
    return cap_h * 3600


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
    """
    Objetivo semanal: el contrato con una variación MÍNIMA de minutos (estable por
    empleado y semana), para que no quede clavado y parezca natural, pero sin
    alejarse del contrato (el cliente quiere que la semana cuadre).
    """
    required = int((user.weekly_hours or 0) * 3600)
    if required <= 0:
        return 0
    off_min = _stable_signed_offset(user.id, week_start, REG_SEED + ":target", REG_TARGET_JITTER_MIN)
    return max(required + off_min * 60, 0)


def _clear_week_alerts(user: User, week_days: list[date]) -> None:
    """
    El cliente NO quiere que se muestren horas extra ni descuadres: se gestionan
    en otra aplicación. La regularización, por tanto, no genera ninguna alerta y
    además borra las que hubiera de la semana procesada (de la lógica anterior).
    """
    OvertimeAlert.query.filter(
        OvertimeAlert.user_id == user.id,
        OvertimeAlert.date >= week_days[0],
        OvertimeAlert.date <= week_days[-1],
    ).delete(synchronize_session=False)


def _distribute_capped(
    target: int, days: list[date], cap_i: dict[date, int], user: User
) -> dict[date, int]:
    """
    Reparte 'target' segundos entre 'days' de modo que:
      - ningún día supere su tope 'cap_i[día]' (tope del contrato, o el hueco
        real hasta las 23:59 si la entrada es tardía),
      - ningún día baje de MIN_DAY_SECONDS,
      - la suma sea EXACTA a 'target' (mientras haya hueco), con una variación
        natural de minutos por día (sesgo estable por empleado y día).
    Si el target no cabe ni llenando todos los días al tope, la semana queda lo
    más alta posible (best-effort); no se genera aviso (cliente).
    """
    if not days or target <= 0:
        return {d: 0 for d in days}

    n = len(days)
    base = target / n
    result: dict[date, int] = {}
    for d in days:
        bias = 60 * _stable_signed_offset(user.id, d, REG_SEED + ":dur", REG_DURATION_JITTER_MIN)
        val = _round_min(base + bias)
        result[d] = max(MIN_DAY_SECONDS, min(val, cap_i[d]))

    # Corrige minuto a minuto hasta cuadrar el total, respetando [MIN, cap_i].
    days_sorted = sorted(days)
    diff = target - sum(result.values())
    guard = 0
    while abs(diff) >= 60 and guard < 200000:
        moved = False
        for d in days_sorted:
            if diff >= 60 and result[d] + 60 <= cap_i[d]:
                result[d] += 60
                diff -= 60
                moved = True
            elif diff <= -60 and result[d] - 60 >= MIN_DAY_SECONDS:
                result[d] -= 60
                diff += 60
                moved = True
            if abs(diff) < 60:
                break
        guard += 1
        if not moved:
            break
    return result


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

    # El cliente NO quiere horas extra ni descuadres: la semana debe CUADRAR al
    # contrato y ningún día puede superar el tope, capando también los días REALES.
    # Por eso ya no se conservan intactos los días "sólidos": todos los días con
    # actividad se recalculan (preservando la ENTRADA real) y se capan al máximo.
    _clear_week_alerts(user, week_days)

    recs_by_day: dict[date, list[TimeRecord]] = {}
    for r in records:
        recs_by_day.setdefault(r.date, []).append(r)

    # Día con actividad -> entrada real a preservar (la más temprana; incluida la
    # preservada en regularizaciones anteriores, RGE). Sin entrada real => None.
    soft_days: dict[date, TimeRecord | None] = {}
    for day, day_recs in recs_by_day.items():
        real_ins = [r for r in day_recs if _has_real_check_in(r)]
        soft_days[day] = min(real_ins, key=lambda r: r.check_in) if real_ins else None

    cap = _max_daily_seconds(user)
    target_seconds = _weekly_target_seconds(user, week_start)
    # El objetivo se acota a lo que permiten los topes (tope × días laborables),
    # así nunca "sobran" horas: p. ej. 40h con tope 8h -> máximo real 40h.
    if cap > 0:
        target_seconds = min(target_seconds, cap * MAX_REG_WORKDAYS)

    # Días candidatos: aquellos donde el empleado tuvo actividad (día activo y no
    # bloqueado por ausencia). Solo los que dejan hueco >= MIN hasta las 23:59.
    def _day_cap(day: date, keep_in: TimeRecord | None) -> int:
        c = cap if cap > 0 else target_seconds
        if keep_in and keep_in.check_in:
            room = int((datetime.combine(day, dt_time(23, 59, 59)) - keep_in.check_in).total_seconds())
            c = min(c, room)
        return c

    soft_presence = sorted(
        d for d in soft_days
        if _is_employee_active_on_day(user, d)
        and not (status_by_date.get(d) and status_by_date[d].status in BLOCKING_STATUSES)
        and _day_cap(d, soft_days[d]) >= MIN_DAY_SECONDS
    )

    # Nº de días objetivo: al menos el típico del contrato y nunca menos de los
    # necesarios para cuadrar sin superar el tope (ceil(target/tope)). Si vino en
    # pocos días, se completan con días generados hasta cuadrar.
    typical = max(_target_workday_count(user.weekly_hours or 0), 1)
    cap_days = -(-target_seconds // cap) if cap > 0 else typical  # ceil(target/tope)
    need_days = max(typical, cap_days)
    target_days = list(soft_presence)
    if len(target_days) < need_days and target_seconds > 0:
        target_days = _extend_with_generated_days(
            user, week_days, set(target_days), status_by_date, need_days - len(target_days)
        )
        target_days = sorted(set(soft_presence) | set(target_days))

    # Borra TODOS los registros de la semana (se recrean capados y cuadrados).
    for day, day_recs in recs_by_day.items():
        for r in day_recs:
            db.session.delete(r)
            ur.removed_records += 1

    if target_seconds <= 0 or not target_days:
        for day in list(soft_days):
            _sync_status_after_clear(status_by_date, day, {})
        return ur

    # Reparte el objetivo entre los días, cuadrando exacto dentro de [MIN, tope].
    cap_by_day = {d: _day_cap(d, soft_days.get(d)) for d in target_days}
    dur_by_day = _distribute_capped(target_seconds, target_days, cap_by_day, user)

    templates = _templates_by_weekday(_user_history_records(user.id, week_start), "histórico empleado")
    group_templates, _ = _get_group_history(user, week_start, pattern_cache)

    for day in target_days:
        dur = dur_by_day.get(day, 0)
        if dur < MIN_DAY_SECONDS:
            continue
        weekday = day.weekday()

        # Entrada: la real si la hay (se conserva), si no una plausible del patrón
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
            # Una entrada REAL no se mueve (su hueco ya está acotado en cap_by_day);
            # solo las entradas generadas se adelantan para respetar la duración.
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

    # Días con actividad que quedaron fuera del reparto: limpia su estado colgante.
    for day in list(soft_days):
        if day not in target_days:
            _sync_status_after_clear(status_by_date, day, {})

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
