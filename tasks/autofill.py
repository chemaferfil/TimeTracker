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
BLOCKING_STATUSES = {"Baja", "Ausente", "Vacaciones"}
HISTORY_WEEKS = 8
JITTER_MINUTES = 5
WEEK_DAYS = 7


@dataclass
class ShiftTemplate:
    weekday: int
    start_seconds: int
    duration_seconds: int
    count: int
    source: str


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
    pattern_cache: dict[tuple[str, str, int], dict[int, ShiftTemplate]] = {}

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
    pattern_cache: dict[tuple[str, str, int], dict[int, ShiftTemplate]],
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
    remaining_seconds = max(required_seconds - worked_seconds, 0)
    user_result.remaining_seconds = remaining_seconds
    if remaining_seconds <= 0:
        return user_result

    record_dates = {record.date for record in records}
    final_work_dates = set(record_dates)
    if not _has_two_consecutive_days_off(final_work_dates, week_days):
        user_result.skipped_reason = "la semana ya no tiene dos días seguidos libres"
        return user_result

    user_templates = _build_user_history_templates(user.id, week_start)
    group_templates = _get_group_templates(user, week_start, pattern_cache)
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
        seconds_to_create = min(remaining_seconds, template.duration_seconds)
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

    if remaining_seconds > 0 and user_result.created_records == 0:
        user_result.skipped_reason = "sin días disponibles para autofichar"
    elif remaining_seconds > 0:
        user_result.skipped_reason = "no se pudieron completar todas las horas"

    return user_result


def _build_user_history_templates(user_id: int, week_start: date) -> dict[int, ShiftTemplate]:
    history_start = week_start - timedelta(days=HISTORY_WEEKS * 7)
    records = TimeRecord.query.filter(
        TimeRecord.user_id == user_id,
        TimeRecord.date >= history_start,
        TimeRecord.date < week_start,
        TimeRecord.check_in.isnot(None),
        TimeRecord.check_out.isnot(None),
    ).all()
    return _templates_by_weekday(records, "histórico empleado")


def _get_group_templates(
    user: User,
    week_start: date,
    cache: dict[tuple[str, str, int], dict[int, ShiftTemplate]],
) -> dict[int, ShiftTemplate]:
    weekly_hours = int(user.weekly_hours or 0)
    category = user.categoria or ""
    primary_key = ("category", category, weekly_hours)
    if primary_key not in cache:
        cache[primary_key] = _query_group_templates(
            week_start,
            weekly_hours=weekly_hours,
            category=user.categoria,
        )
    if cache[primary_key]:
        return cache[primary_key]

    fallback_key = ("weekly_hours", "", weekly_hours)
    if fallback_key not in cache:
        cache[fallback_key] = _query_group_templates(
            week_start,
            weekly_hours=weekly_hours,
            category=None,
        )
    return cache[fallback_key]


def _query_group_templates(
    week_start: date,
    weekly_hours: int,
    category: str | None,
) -> dict[int, ShiftTemplate]:
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

    source = "histórico categoría" if category else "histórico jornada"
    return _templates_by_weekday(query.all(), source)


def _templates_by_weekday(records: list[TimeRecord], source: str) -> dict[int, ShiftTemplate]:
    grouped: dict[int, list[tuple[int, int]]] = {}
    for record in records:
        if LEGACY_AUTO_FILL_NOTE in (record.notes or ""):
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


def _template_from_records(records: list[TimeRecord], source: str) -> ShiftTemplate | None:
    values = []
    for record in records:
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


def _stable_minute_offset(user_id: int, day: date) -> int:
    return _stable_number(user_id, day.isoformat(), AUTO_FILL_SEED) % (JITTER_MINUTES * 2 + 1) - JITTER_MINUTES


def _stable_number(*parts) -> int:
    digest = sha256(":".join(str(part) for part in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")

