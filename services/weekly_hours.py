from datetime import date, timedelta

from models.database import db
from models.models import TimeRecord, User, UserWeeklyHoursPeriod


def week_end_for(day: date) -> date:
    return day - timedelta(days=day.weekday()) + timedelta(days=6)


def weekly_hours_for_date(user: User | None, target_date: date | None) -> int:
    if not user:
        return 0
    if not target_date:
        return int(user.weekly_hours or 0)

    periods = list(getattr(user, "weekly_hours_periods", []) or [])
    matching = [
        period
        for period in periods
        if period.start_date <= target_date
        and (period.end_date is None or period.end_date >= target_date)
    ]
    if matching:
        return int(sorted(matching, key=lambda period: period.start_date)[-1].weekly_hours)

    period = (
        UserWeeklyHoursPeriod.query
        .filter(
            UserWeeklyHoursPeriod.user_id == user.id,
            UserWeeklyHoursPeriod.start_date <= target_date,
            (
                (UserWeeklyHoursPeriod.end_date.is_(None))
                | (UserWeeklyHoursPeriod.end_date >= target_date)
            ),
        )
        .order_by(UserWeeklyHoursPeriod.start_date.desc())
        .first()
    )
    if period:
        return int(period.weekly_hours)

    return int(user.weekly_hours or 0)


def weekly_hours_for_week(user: User | None, day_in_week: date | None) -> int:
    if not day_in_week:
        return weekly_hours_for_date(user, None)
    return weekly_hours_for_date(user, week_end_for(day_in_week))


def available_weekly_hours_for_users(users: list[User]) -> list[int]:
    values = set()
    for user in users:
        if user.weekly_hours is not None:
            values.add(int(user.weekly_hours))
        for period in getattr(user, "weekly_hours_periods", []) or []:
            values.add(int(period.weekly_hours))
    return sorted(values)


def ensure_weekly_hours_history(user: User, start_date: date | None = None):
    periods = list(getattr(user, "weekly_hours_periods", []) or [])
    if periods:
        return

    initial_start = start_date or _initial_period_start(user) or date.today()
    db.session.add(UserWeeklyHoursPeriod(
        user=user,
        weekly_hours=int(user.weekly_hours or 0),
        start_date=initial_start,
        end_date=None,
    ))


def apply_weekly_hours_change(
    user: User,
    new_weekly_hours: int,
    effective_date: date | None = None,
    previous_weekly_hours: int | None = None,
):
    effective_date = effective_date or date.today()
    previous_hours = (
        int(previous_weekly_hours)
        if previous_weekly_hours is not None
        else int(user.weekly_hours or 0)
    )
    new_weekly_hours = int(new_weekly_hours)

    periods = list(getattr(user, "weekly_hours_periods", []) or [])
    if not periods:
        initial_start = _initial_period_start(user) or effective_date
        if initial_start < effective_date:
            db.session.add(UserWeeklyHoursPeriod(
                user=user,
                weekly_hours=previous_hours,
                start_date=initial_start,
                end_date=effective_date - timedelta(days=1),
            ))
        else:
            effective_date = min(initial_start, effective_date)

    replacement_period = None
    for period in list(getattr(user, "weekly_hours_periods", []) or []):
        if period.start_date == effective_date:
            replacement_period = period
            period.weekly_hours = new_weekly_hours
            period.end_date = None
            continue
        if period.start_date > effective_date:
            db.session.delete(period)
            continue
        if period.end_date is None or period.end_date >= effective_date:
            period.end_date = effective_date - timedelta(days=1)

    if replacement_period is None:
        db.session.add(UserWeeklyHoursPeriod(
            user=user,
            weekly_hours=new_weekly_hours,
            start_date=effective_date,
            end_date=None,
        ))
    user.weekly_hours = new_weekly_hours


def _initial_period_start(user: User) -> date | None:
    candidates = []
    if user.hire_date:
        candidates.append(user.hire_date)

    if user.id:
        first_record = (
            TimeRecord.query
            .filter(TimeRecord.user_id == user.id)
            .order_by(TimeRecord.date.asc())
            .first()
        )
        if first_record:
            candidates.append(first_record.date)

    if user.created_at:
        candidates.append(user.created_at.date())

    return min(candidates) if candidates else None
