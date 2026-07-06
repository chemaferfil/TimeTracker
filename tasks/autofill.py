"""
Weekly auto-fill logic for missing employee time records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
from hashlib import sha256

from flask import current_app

from models.database import db
from models.models import EmployeeStatus, TimeRecord, User


AUTO_FILL_RECORD_NOTE = "AA"
LEGACY_AUTO_FILL_NOTE = "Autofichaje automático"
AUTO_FILL_SEED = "autofill"
AUTO_CLOSE_SEED = "autoclose"
TOP_UP_SEED = "topup"
DURATION_SEED = "duration"
SHORTFALL_SEED = "shortfall"
BLOCKING_STATUSES = {"Baja", "Ausente", "Vacaciones"}
HISTORY_WEEKS = 8
JITTER_MINUTES = 5
DURATION_JITTER_MINUTES = 6
WEEK_DAYS = 7
TOP_UP_BREAK_MINUTES = 60
MIN_TOP_UP_SECONDS = 30 * 60
MIN_FILL_SECONDS = 15 * 60
# La semana no debe cuadrar clavada al contrato: se deja por debajo un margen
# variable por empleado y semana (entre estos dos porcentajes, en puntos básicos).
WEEKLY_SHORTFALL_MIN_BP = 150
WEEKLY_SHORTFALL_MAX_BP = 600
LEGACY_AUTO_CLOSE_TIME = dt_time(23, 59, 59)


@dataclass
class ShiftTemplate:
    weekday: int
    start_seconds: int
    duration_seconds: int
    count: int
    source: str


@dataclass
class DayPattern:
    weekday: int
    total_seconds: int
    second_start_seconds: int | None


@dataclass
class AutoFillUserResult:
    user_id: int
    username: str
    full_name: str
    created_records: int = 0
    created_seconds: int = 0
    remaining_seconds: int = 0
    skipped_reason: str | None = None
    pattern_source: str | None = None


@dataclass
class AutoFillResult:
    week_start: date
    processed_users: int = 0
    created_records: int = 0
    created_seconds: int = 0
    user_results: list[AutoFillUserResult] = field(default_factory=list)

    @property
    def week_end(self):
        return self.week_start + timedelta(days=6)

    @property
    def skipped_users(self):
        return [item for item in self.user_results if item.skipped_reason]


def _get_app(explicit_app=None):
    if explicit_app is not None:
        return explicit_app
    try:
        return current_app._get_current_object()
    except RuntimeError:
        try:
            from main import app as main_app
            return main_app
        except Exception:
            return None


def normalize_week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def autofill_previous_completed_week(reference_date: date | None = None, app=None):
    today = reference_date or date.today()
    current_week_start = normalize_week_start(today)
    return autofill_week(current_week_start - timedelta(days=7), app=app)


def autofill_week(
    week_start: date,
    app=None,
    centro: str | None = None,
    modified_by: int | None = None,
    commit: bool = True,
) -> AutoFillResult:
    """
    Fill missing records for active non-admin employees in a complete week.

    The operation is idempotent because it only creates records on dates where
    the employee has no TimeRecord yet.
    """
    app = _get_app(app)
    if app is None:
        raise RuntimeError("Flask app not available for autofill_week")

    with app.app_context():
        try:
            result = _autofill_week_impl(
                normalize_week_start(week_start),
                centro=centro,
                modified_by=modified_by,
            )
            if commit:
                db.session.commit()
            return result
        except Exception:
            db.session.rollback()
            raise


def _autofill_week_impl(
    week_start: date,
    centro: str | None = None,
    modified_by: int | None = None,
) -> AutoFillResult:
    week_days = [week_start + timedelta(days=i) for i in range(WEEK_DAYS)]
    week_end = week_days[-1]
    result = AutoFillResult(week_start=week_start)
    pattern_cache: dict[tuple[str, str, int], tuple[dict[int, ShiftTemplate], dict[int, DayPattern]]] = {}

    query = User.query.filter(
        User.is_admin.is_(False),
        User.is_active.is_(True),
    )
    if centro:
        query = query.filter(User.centro == centro)

    users = query.order_by(User.full_name.asc(), User.username.asc()).all()
    result.processed_users = len(users)

    for user in users:
        user_result = _autofill_user_week(
            user,
            week_days,
            week_start,
            week_end,
            modified_by,
            pattern_cache,
        )
        result.user_results.append(user_result)
        result.created_records += user_result.created_records
        result.created_seconds += user_result.created_seconds

    return result


def _autofill_user_week(
    user: User,
    week_days: list[date],
    week_start: date,
    week_end: date,
    modified_by,
    pattern_cache: dict[tuple[str, str, int], tuple[dict[int, ShiftTemplate], dict[int, DayPattern]]],
):
    user_result = AutoFillUserResult(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    required_seconds = int((user.weekly_hours or 0) * 3600)
    if required_seconds <= 0:
        return user_result

    if user.hire_date and user.hire_date > week_end:
        user_result.skipped_reason = "fuera de rango de alta"
        return user_result
    if user.termination_date and user.termination_date < week_start:
        user_result.skipped_reason = "fuera de rango de baja"
        return user_result

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
    status_by_date = {status.date: status for status in statuses}

    worked_seconds = sum(_record_seconds(record) for record in records)
    if worked_seconds >= required_seconds:
        user_result.remaining_seconds = 0
        return user_result

    # Objetivo semanal: por debajo del contrato, con un margen variable por
    # empleado y semana para que ninguna semana quede clavada a las horas exactas.
    target_seconds = _weekly_target_seconds(user, week_start, required_seconds)

    history_records = _user_history_records(user.id, week_start)
    user_templates = _templates_by_weekday(history_records, "histórico empleado")
    user_day_patterns = _day_patterns_by_weekday(history_records)
    group_templates, group_day_patterns = _get_group_history(user, week_start, pattern_cache)

    records_by_date: dict[date, list[TimeRecord]] = {}
    for record in records:
        records_by_date.setdefault(record.date, []).append(record)

    # 1) Completa los días con fichaje parcial real hasta su jornada diaria
    #    normal (presupuesto = lo que falta para el contrato).
    created_topup = _top_up_partial_days(
        user,
        week_days,
        records_by_date,
        status_by_date,
        required_seconds - worked_seconds,
        user_day_patterns,
        group_day_patterns,
        modified_by,
        user_result,
    )
    worked_seconds += created_topup

    # 2) Rellena los días vacíos hasta el objetivo semanal (por debajo del contrato).
    remaining_seconds = max(target_seconds - worked_seconds, 0)
    user_result.remaining_seconds = remaining_seconds
    if remaining_seconds < MIN_FILL_SECONDS:
        return user_result

    record_dates = set(records_by_date)
    final_work_dates = set(record_dates)
    if not _has_two_consecutive_days_off(final_work_dates, week_days):
        user_result.skipped_reason = "la semana ya no tiene dos días seguidos libres"
        return user_result

    current_week_template = _template_from_records(records, "semana actual")
    candidate_weekdays = _candidate_weekdays(user, user_templates, group_templates)

    for weekday in candidate_weekdays:
        if remaining_seconds <= 0:
            break

        day = week_days[weekday]
        if day in record_dates:
            continue
        if not _is_employee_active_on_day(user, day):
            continue

        status = status_by_date.get(day)
        if status and status.status in BLOCKING_STATUSES:
            continue

        candidate_work_dates = final_work_dates | {day}
        if not _has_two_consecutive_days_off(candidate_work_dates, week_days):
            continue

        template = _select_template(
            user,
            weekday,
            remaining_seconds,
            user_templates,
            group_templates,
            current_week_template,
        )
        seconds_to_create = _fill_duration_seconds(
            user,
            day,
            template.duration_seconds,
            remaining_seconds,
        )
        if seconds_to_create < MIN_FILL_SECONDS:
            break
        check_in, check_out = _build_shifted_record_times(
            user.id,
            day,
            template.start_seconds,
            seconds_to_create,
        )

        db.session.add(TimeRecord(
            user_id=user.id,
            check_in=check_in,
            check_out=check_out,
            date=day,
            notes=AUTO_FILL_RECORD_NOTE,
            modified_by=modified_by,
        ))

        if status:
            status.status = "Trabajado"
            status.entry_time = check_in.time()
            status.exit_time = check_out.time()
        else:
            db.session.add(EmployeeStatus(
                user_id=user.id,
                date=day,
                status="Trabajado",
                entry_time=check_in.time(),
                exit_time=check_out.time(),
                notes=AUTO_FILL_RECORD_NOTE,
            ))

        remaining_seconds -= seconds_to_create
        final_work_dates.add(day)
        user_result.created_records += 1
        user_result.created_seconds += seconds_to_create
        user_result.remaining_seconds = remaining_seconds
        user_result.pattern_source = template.source

    if user_result.created_records == 0 and remaining_seconds >= MIN_FILL_SECONDS:
        user_result.skipped_reason = "sin días disponibles para autofichar"
    elif remaining_seconds >= MIN_FILL_SECONDS:
        user_result.skipped_reason = "semana incompleta: sin días disponibles suficientes"

    return user_result


def _user_history_records(user_id: int, week_start: date) -> list[TimeRecord]:
    history_start = week_start - timedelta(days=HISTORY_WEEKS * 7)
    return TimeRecord.query.filter(
        TimeRecord.user_id == user_id,
        TimeRecord.date >= history_start,
        TimeRecord.date < week_start,
        TimeRecord.check_in.isnot(None),
        TimeRecord.check_out.isnot(None),
    ).all()


def _get_group_history(
    user: User,
    week_start: date,
    cache: dict[tuple[str, str, int], tuple[dict[int, ShiftTemplate], dict[int, DayPattern]]],
) -> tuple[dict[int, ShiftTemplate], dict[int, DayPattern]]:
    weekly_hours = int(user.weekly_hours or 0)
    category = user.categoria or ""
    primary_key = ("category", category, weekly_hours)
    if primary_key not in cache:
        cache[primary_key] = _build_group_history(
            week_start,
            weekly_hours=weekly_hours,
            category=user.categoria,
        )
    templates, day_patterns = cache[primary_key]
    if templates or day_patterns:
        return cache[primary_key]

    fallback_key = ("weekly_hours", "", weekly_hours)
    if fallback_key not in cache:
        cache[fallback_key] = _build_group_history(
            week_start,
            weekly_hours=weekly_hours,
            category=None,
        )
    return cache[fallback_key]


def _build_group_history(
    week_start: date,
    weekly_hours: int,
    category: str | None,
) -> tuple[dict[int, ShiftTemplate], dict[int, DayPattern]]:
    history_start = week_start - timedelta(days=HISTORY_WEEKS * 7)
    query = (
        TimeRecord.query
        .join(User, TimeRecord.user_id == User.id)
        .filter(
            User.is_admin.is_(False),
            User.is_active.is_(True),
            User.weekly_hours == weekly_hours,
            TimeRecord.date >= history_start,
            TimeRecord.date < week_start,
            TimeRecord.check_in.isnot(None),
            TimeRecord.check_out.isnot(None),
        )
    )
    if category:
        query = query.filter(User.categoria == category)

    records = query.all()
    source = "histórico categoría" if category else "histórico jornada"
    return _templates_by_weekday(records, source), _day_patterns_by_weekday(records)


def _is_real_punch(record: TimeRecord) -> bool:
    """A record that reflects a real clock-in (not one we generated)."""
    return AUTO_FILL_RECORD_NOTE not in (record.notes or "")


def _is_template_record(record: TimeRecord) -> bool:
    if LEGACY_AUTO_FILL_NOTE in (record.notes or ""):
        return False
    if not record.check_in or not record.check_out:
        return False
    # Los cierres automáticos al final del día no representan turnos reales.
    if record.check_out.time() == LEGACY_AUTO_CLOSE_TIME:
        return False
    return True


def _templates_by_weekday(records: list[TimeRecord], source: str) -> dict[int, ShiftTemplate]:
    grouped: dict[int, list[tuple[int, int]]] = {}
    for record in records:
        if not _is_template_record(record):
            continue
        duration_seconds = _record_seconds(record)
        if duration_seconds <= 0:
            continue
        weekday = record.date.weekday()
        grouped.setdefault(weekday, []).append((
            _time_to_seconds(record.check_in.time()),
            duration_seconds,
        ))

    templates = {}
    for weekday, values in grouped.items():
        templates[weekday] = ShiftTemplate(
            weekday=weekday,
            start_seconds=_median_int([item[0] for item in values]),
            duration_seconds=_median_int([item[1] for item in values]),
            count=len(values),
            source=source,
        )
    return templates


def _day_patterns_by_weekday(records: list[TimeRecord]) -> dict[int, DayPattern]:
    per_day: dict[tuple[int, date], list[TimeRecord]] = {}
    for record in records:
        if not _is_template_record(record) or _record_seconds(record) <= 0:
            continue
        per_day.setdefault((record.user_id, record.date), []).append(record)

    totals: dict[int, list[int]] = {}
    second_starts: dict[int, list[int]] = {}
    for (_, day), day_records in per_day.items():
        weekday = day.weekday()
        totals.setdefault(weekday, []).append(
            sum(_record_seconds(record) for record in day_records)
        )
        if len(day_records) > 1:
            ordered = sorted(day_records, key=lambda record: record.check_in)
            second_starts.setdefault(weekday, []).append(
                _time_to_seconds(ordered[1].check_in.time())
            )

    patterns = {}
    for weekday, values in totals.items():
        starts = second_starts.get(weekday)
        patterns[weekday] = DayPattern(
            weekday=weekday,
            total_seconds=_median_int(values),
            second_start_seconds=_median_int(starts) if starts else None,
        )
    return patterns


def _expected_day_seconds(
    user: User,
    weekday: int,
    user_day_patterns: dict[int, DayPattern],
    group_day_patterns: dict[int, DayPattern],
) -> int:
    pattern = user_day_patterns.get(weekday) or group_day_patterns.get(weekday)
    if pattern:
        return pattern.total_seconds
    if user_day_patterns:
        return _median_int([item.total_seconds for item in user_day_patterns.values()])
    if group_day_patterns:
        return _median_int([item.total_seconds for item in group_day_patterns.values()])
    weekly_hours = user.weekly_hours or 0
    return int((weekly_hours * 3600) / max(_target_workday_count(weekly_hours), 1))


def _second_shift_start_seconds(
    weekday: int,
    user_day_patterns: dict[int, DayPattern],
    group_day_patterns: dict[int, DayPattern],
) -> int | None:
    for patterns in (user_day_patterns, group_day_patterns):
        pattern = patterns.get(weekday)
        if pattern and pattern.second_start_seconds is not None:
            return pattern.second_start_seconds
    for patterns in (user_day_patterns, group_day_patterns):
        starts = [
            item.second_start_seconds
            for item in patterns.values()
            if item.second_start_seconds is not None
        ]
        if starts:
            return _median_int(starts)
    return None


def _top_up_partial_days(
    user: User,
    week_days: list[date],
    records_by_date: dict[date, list[TimeRecord]],
    status_by_date: dict[date, EmployeeStatus],
    budget_seconds: int,
    user_day_patterns: dict[int, DayPattern],
    group_day_patterns: dict[int, DayPattern],
    modified_by,
    user_result: AutoFillUserResult,
) -> int:
    """
    Complete days where the employee clocked part of the day (e.g. only the
    morning shift) by adding the missing shift up to the expected daily total.

    Returns the total number of seconds created. Only days that contain at
    least one real punch are eligible, so fully auto-generated days are never
    re-completed (keeping the operation idempotent).
    """
    remaining_budget = budget_seconds
    created_total = 0
    for day in week_days:
        if remaining_budget < MIN_FILL_SECONDS:
            break

        day_records = records_by_date.get(day)
        if not day_records:
            continue
        if any(record.check_in is None or record.check_out is None for record in day_records):
            continue
        if not any(_is_real_punch(record) for record in day_records):
            continue
        if not _is_employee_active_on_day(user, day):
            continue

        status = status_by_date.get(day)
        if status and status.status in BLOCKING_STATUSES:
            continue

        worked_day = sum(_record_seconds(record) for record in day_records)
        expected_day = _expected_day_seconds(
            user, day.weekday(), user_day_patterns, group_day_patterns
        )
        deficit = expected_day - worked_day
        if deficit < MIN_TOP_UP_SECONDS:
            continue

        seconds_to_create = _round_to_minute(min(deficit, remaining_budget))
        if seconds_to_create < MIN_FILL_SECONDS:
            continue

        last_out = max(record.check_out for record in day_records)
        start = _top_up_start(user, day, last_out, user_day_patterns, group_day_patterns)
        max_end = datetime.combine(day, dt_time(23, 59, 0))
        if start >= max_end:
            continue
        end = min(start + timedelta(seconds=seconds_to_create), max_end)
        created_seconds = int((end - start).total_seconds())
        if created_seconds < MIN_FILL_SECONDS:
            continue

        first_in = min(record.check_in for record in day_records)
        db.session.add(TimeRecord(
            user_id=user.id,
            check_in=start,
            check_out=end,
            date=day,
            notes=AUTO_FILL_RECORD_NOTE,
            modified_by=modified_by,
        ))

        if status:
            status.status = "Trabajado"
            if status.entry_time is None:
                status.entry_time = first_in.time()
            status.exit_time = end.time()
        else:
            db.session.add(EmployeeStatus(
                user_id=user.id,
                date=day,
                status="Trabajado",
                entry_time=first_in.time(),
                exit_time=end.time(),
                notes=AUTO_FILL_RECORD_NOTE,
            ))

        remaining_budget -= created_seconds
        created_total += created_seconds
        user_result.created_records += 1
        user_result.created_seconds += created_seconds
        user_result.pattern_source = "completado de día parcial"

    return created_total


def _top_up_start(
    user: User,
    day: date,
    last_out: datetime,
    user_day_patterns: dict[int, DayPattern],
    group_day_patterns: dict[int, DayPattern],
) -> datetime:
    jitter = timedelta(minutes=_stable_minute_offset(user.id, day, TOP_UP_SEED))
    minimum_start = last_out + timedelta(minutes=15)

    second_start = _second_shift_start_seconds(
        day.weekday(), user_day_patterns, group_day_patterns
    )
    if second_start is not None:
        start = datetime.combine(day, dt_time(0, 0)) + timedelta(seconds=second_start) + jitter
        if start >= minimum_start:
            return _floor_to_minute(start)

    return _floor_to_minute(last_out + timedelta(minutes=TOP_UP_BREAK_MINUTES) + jitter)


def estimate_auto_close_time(record: TimeRecord) -> datetime | None:
    """
    Estimate a plausible check-out for an open record, based on the employee's
    typical shift duration (own history, then group history, then weekly hours).
    Returns None when no sensible estimate can be made.
    """
    if not record.check_in:
        return None
    user = db.session.get(User, record.user_id)
    if user is None:
        return None

    week_start = normalize_week_start(record.date)
    history_records = _user_history_records(user.id, week_start)
    user_templates = _templates_by_weekday(history_records, "histórico empleado")
    weekday = record.date.weekday()

    template = user_templates.get(weekday)
    if template is None:
        group_templates, _ = _get_group_history(user, week_start, {})
        template = (
            group_templates.get(weekday)
            or _strongest_template(user_templates)
            or _strongest_template(group_templates)
        )

    if template is not None:
        duration_seconds = template.duration_seconds
    else:
        weekly_hours = user.weekly_hours or 0
        if weekly_hours <= 0:
            return None
        duration_seconds = int((weekly_hours * 3600) / _target_workday_count(weekly_hours))

    duration_seconds += 60 * _stable_minute_offset(user.id, record.date, AUTO_CLOSE_SEED)
    duration_seconds = max(duration_seconds, 60)

    check_out = record.check_in + timedelta(seconds=duration_seconds)
    max_end = datetime.combine(record.date, dt_time(23, 59, 59))
    if check_out > max_end:
        check_out = max_end
    if check_out <= record.check_in:
        return None
    return check_out


def _template_from_records(records: list[TimeRecord], source: str) -> ShiftTemplate | None:
    values = []
    for record in records:
        if not _is_template_record(record):
            continue
        duration_seconds = _record_seconds(record)
        if duration_seconds <= 0 or not record.check_in:
            continue
        values.append((_time_to_seconds(record.check_in.time()), duration_seconds))
    if not values:
        return None
    return ShiftTemplate(
        weekday=-1,
        start_seconds=_median_int([item[0] for item in values]),
        duration_seconds=_median_int([item[1] for item in values]),
        count=len(values),
        source=source,
    )


def _candidate_weekdays(
    user: User,
    user_templates: dict[int, ShiftTemplate],
    group_templates: dict[int, ShiftTemplate],
) -> list[int]:
    if user_templates:
        return _append_missing_weekdays(_ordered_template_weekdays(user_templates))

    generated = _generated_weekdays(user)
    if group_templates:
        group_rank = _ordered_template_weekdays(group_templates)
        ordered = [weekday for weekday in generated if weekday in group_rank]
        ordered.extend(weekday for weekday in generated if weekday not in ordered)
        ordered.extend(weekday for weekday in group_rank if weekday not in ordered)
        return _append_missing_weekdays(ordered)

    return _append_missing_weekdays(generated)


def _ordered_template_weekdays(templates: dict[int, ShiftTemplate]) -> list[int]:
    return [
        item.weekday
        for item in sorted(
            templates.values(),
            key=lambda template: (-template.count, template.weekday),
        )
    ]


def _append_missing_weekdays(weekdays: list[int]) -> list[int]:
    result = []
    for weekday in weekdays + list(range(WEEK_DAYS)):
        if weekday not in result:
            result.append(weekday)
    return result


def _generated_weekdays(user: User) -> list[int]:
    workday_count = _target_workday_count(user.weekly_hours or 0)
    options = [
        list(range(start, start + workday_count))
        for start in range(0, WEEK_DAYS - workday_count + 1)
    ]
    valid_options = [
        option
        for option in options
        if _has_two_consecutive_days_off(
            {date(2026, 1, 5) + timedelta(days=weekday) for weekday in option},
            [date(2026, 1, 5) + timedelta(days=i) for i in range(WEEK_DAYS)],
        )
    ]
    valid_options = valid_options or options
    index = _stable_number(user.id, user.categoria or "", user.weekly_hours or 0) % len(valid_options)
    return valid_options[index]


def _target_workday_count(weekly_hours: int) -> int:
    if weekly_hours >= 25:
        return 5
    if weekly_hours >= 20:
        return 4
    if weekly_hours >= 15:
        return 3
    if weekly_hours >= 8:
        return 2
    return 1


def _select_template(
    user: User,
    weekday: int,
    remaining_seconds: int,
    user_templates: dict[int, ShiftTemplate],
    group_templates: dict[int, ShiftTemplate],
    current_week_template: ShiftTemplate | None,
) -> ShiftTemplate:
    exact_template = user_templates.get(weekday) or group_templates.get(weekday)
    if exact_template:
        return exact_template

    generic_template = (
        current_week_template
        or _strongest_template(user_templates)
        or _strongest_template(group_templates)
    )
    if generic_template:
        return generic_template

    workday_count = _target_workday_count(user.weekly_hours or 0)
    default_seconds = max(60, int(((user.weekly_hours or 0) * 3600) / max(workday_count, 1)))
    return ShiftTemplate(
        weekday=weekday,
        start_seconds=_generated_start_seconds(user),
        duration_seconds=min(default_seconds, remaining_seconds),
        count=1,
        source="patrón generado",
    )


def _strongest_template(templates: dict[int, ShiftTemplate]) -> ShiftTemplate | None:
    if not templates:
        return None
    return sorted(
        templates.values(),
        key=lambda template: (-template.count, template.weekday),
    )[0]


def _generated_start_seconds(user: User) -> int:
    base_hour = 8 + (_stable_number("start", user.id, user.categoria or "") % 4)
    return base_hour * 3600


def _record_seconds(record: TimeRecord) -> int:
    if not record.check_in or not record.check_out:
        return 0
    seconds = int((record.check_out - record.check_in).total_seconds())
    return max(seconds, 0)


def _is_employee_active_on_day(user: User, day: date) -> bool:
    if user.hire_date and day < user.hire_date:
        return False
    if user.termination_date and day > user.termination_date:
        return False
    return True


def _has_two_consecutive_days_off(work_dates: set[date], week_days: list[date]) -> bool:
    consecutive = 0
    for day in week_days:
        if day in work_dates:
            consecutive = 0
        else:
            consecutive += 1
            if consecutive >= 2:
                return True
    return False


def _build_shifted_record_times(
    user_id: int,
    day: date,
    start_seconds: int,
    duration_seconds: int,
) -> tuple[datetime, datetime]:
    shift = timedelta(minutes=_stable_minute_offset(user_id, day))
    duration = timedelta(seconds=duration_seconds)
    start = datetime.combine(day, dt_time(0, 0, 0)) + timedelta(seconds=start_seconds) + shift
    end = start + duration

    min_start = datetime.combine(day, dt_time(0, 0, 0))
    max_end = datetime.combine(day, dt_time(23, 59, 59))

    if start < min_start:
        start = min_start
        end = start + duration
    if end > max_end:
        end = max_end
        start = end - duration

    if start.date() != day or end.date() != day or end <= start:
        raise ValueError("No se pudo generar un fichaje dentro del día")
    return start, end


def _time_to_seconds(value: dt_time) -> int:
    return value.hour * 3600 + value.minute * 60 + value.second


def _median_int(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[index]
    return int((ordered[index - 1] + ordered[index]) / 2)


def _stable_signed_offset(user_id: int, day: date, seed: str, span: int) -> int:
    if span <= 0:
        return 0
    return _stable_number(user_id, day.isoformat(), seed) % (span * 2 + 1) - span


def _stable_minute_offset(user_id: int, day: date, seed: str = AUTO_FILL_SEED) -> int:
    return _stable_signed_offset(user_id, day, seed, JITTER_MINUTES)


def _round_to_minute(seconds: int) -> int:
    return (int(seconds) // 60) * 60


def _floor_to_minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _fill_duration_seconds(
    user: User,
    day: date,
    base_seconds: int,
    remaining_seconds: int,
) -> int:
    """
    Duration for a generated full day: the template duration plus a stable
    per-day jitter, rounded to whole minutes. When it would meet or exceed
    the remaining budget, fall back to the remaining amount (floored to the
    minute) so the last day shortens naturally instead of hitting an exact
    second value.
    """
    remaining_floor = _round_to_minute(remaining_seconds)
    if base_seconds >= remaining_seconds:
        return remaining_floor

    offset = 60 * _stable_signed_offset(user.id, day, DURATION_SEED, DURATION_JITTER_MINUTES)
    jittered = _round_to_minute(max(base_seconds + offset, 60))
    if jittered >= remaining_seconds:
        return remaining_floor
    return jittered


def _weekly_target_seconds(user: User, week_start: date, required_seconds: int) -> int:
    """
    Target seconds for the week: the contract hours minus a variable margin so
    no week lands exactly on the contracted hours. The margin depends on the
    employee and the week, so it varies across both and shows no fixed pattern.
    """
    if required_seconds <= 0:
        return 0
    span = WEEKLY_SHORTFALL_MAX_BP - WEEKLY_SHORTFALL_MIN_BP
    basis_points = WEEKLY_SHORTFALL_MIN_BP + (
        _stable_number(user.id, week_start.isoformat(), SHORTFALL_SEED) % (span + 1)
    )
    shortfall = _round_to_minute(int(required_seconds * basis_points / 10000))
    return max(required_seconds - shortfall, 0)


def _stable_number(*parts) -> int:
    digest = sha256(":".join(str(part) for part in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")

