import unittest
from datetime import date, datetime, time, timedelta

from flask import Flask

from models.database import db
from models.models import EmployeeStatus, TimeRecord, User
from tasks.autofill import autofill_week, estimate_auto_close_time
from tasks.scheduler import close_open_record


class AutoFillWeekTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        self.app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        self.app.config["TESTING"] = True
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.week_start = date(2026, 5, 4)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def _user(self, username, weekly_hours, categoria="Reparto"):
        user = User(
            username=username,
            full_name=username.title(),
            email=f"{username}@example.com",
            is_admin=False,
            is_active=True,
            weekly_hours=weekly_hours,
            categoria=categoria,
        )
        user.set_password("secret")
        db.session.add(user)
        db.session.commit()
        return user

    def _record(self, user, day, start_hour=9, hours=5):
        check_in = datetime.combine(day, time(start_hour, 0))
        record = TimeRecord(
            user_id=user.id,
            date=day,
            check_in=check_in,
            check_out=check_in + timedelta(hours=hours),
        )
        db.session.add(record)
        db.session.commit()
        return record

    def _worked_seconds(self, user):
        records = TimeRecord.query.filter_by(user_id=user.id).all()
        return sum(int((r.check_out - r.check_in).total_seconds()) for r in records)

    def _worked_dates(self, user):
        return {
            record.date
            for record in TimeRecord.query.filter_by(user_id=user.id).all()
        }

    def test_uses_employee_history_to_fill_expected_missing_day(self):
        user = self._user("historico10", weekly_hours=10)
        for weeks_back in (1, 2, 3):
            monday = self.week_start - timedelta(days=7 * weeks_back)
            self._record(user, monday, start_hour=9, hours=5)
            self._record(user, monday + timedelta(days=1), start_hour=9, hours=5)

        self._record(user, self.week_start, start_hour=9, hours=5)

        result = autofill_week(self.week_start, app=self.app)

        created = TimeRecord.query.filter_by(
            user_id=user.id,
            date=self.week_start + timedelta(days=1),
        ).first()
        self.assertEqual(result.created_records, 1)
        self.assertIsNotNone(created)
        self.assertEqual(self._worked_seconds(user), 40 * 3600)
        self.assertEqual(created.notes, "AA")

    def test_category_fallback_distributes_new_users_naturally(self):
        template_user = self._user("plantilla20", weekly_hours=20, categoria="Reparto")
        previous_week = self.week_start - timedelta(days=7)
        for offset in (0, 1, 2, 3):
            self._record(template_user, previous_week + timedelta(days=offset), start_hour=10, hours=5)

        new_users = [
            self._user(f"nuevo20_{index}", weekly_hours=20, categoria="Reparto")
            for index in range(6)
        ]

        result = autofill_week(self.week_start, app=self.app)

        patterns = {
            tuple(sorted(self._worked_dates(user)))
            for user in new_users
        }
        self.assertGreaterEqual(result.created_records, 24)
        self.assertGreater(len(patterns), 1)
        for user in new_users:
            self.assertEqual(self._worked_seconds(user), 20 * 3600)

    def test_skips_blocked_status_day(self):
        user = self._user("vacaciones", weekly_hours=8)
        db.session.add(EmployeeStatus(
            user_id=user.id,
            date=self.week_start,
            status="Vacaciones",
        ))
        db.session.commit()

        result = autofill_week(self.week_start, app=self.app)

        self.assertEqual(result.created_records, 2)
        self.assertIsNone(TimeRecord.query.filter_by(user_id=user.id, date=self.week_start).first())

    def test_missing_base_schedule_still_uses_generated_pattern(self):
        user = self._user("sinhorario", weekly_hours=8)

        result = autofill_week(self.week_start, app=self.app)

        self.assertGreater(result.created_records, 0)
        self.assertEqual(TimeRecord.query.filter_by(user_id=user.id).count(), result.created_records)
        self.assertEqual(self._worked_seconds(user), 8 * 3600)

    def _full_history_weeks(self, user, weeks=4, start_hour=9, hours=8, days=5):
        for weeks_back in range(1, weeks + 1):
            monday = self.week_start - timedelta(days=7 * weeks_back)
            for offset in range(days):
                self._record(user, monday + timedelta(days=offset), start_hour=start_hour, hours=hours)

    def test_auto_close_uses_plausible_exit_time(self):
        user = self._user("sinSalida40", weekly_hours=40)
        self._full_history_weeks(user)

        open_record = TimeRecord(
            user_id=user.id,
            date=self.week_start,
            check_in=datetime.combine(self.week_start, time(9, 3)),
        )
        db.session.add(open_record)
        db.session.commit()

        close_open_record(open_record)
        db.session.commit()

        self.assertIsNotNone(open_record.check_out)
        self.assertEqual(open_record.check_out.date(), self.week_start)
        self.assertIn("CA", open_record.notes)
        # Duración típica 8h con jitter de ±5 minutos sobre las 17:03.
        self.assertGreaterEqual(open_record.check_out, datetime.combine(self.week_start, time(16, 58)))
        self.assertLessEqual(open_record.check_out, datetime.combine(self.week_start, time(17, 8)))

    def test_auto_close_falls_back_without_pattern(self):
        user = self._user("sinPatron0", weekly_hours=0)
        open_record = TimeRecord(
            user_id=user.id,
            date=self.week_start,
            check_in=datetime.combine(self.week_start, time(9, 0)),
        )
        db.session.add(open_record)
        db.session.commit()

        close_open_record(open_record)
        db.session.commit()

        self.assertEqual(
            open_record.check_out,
            datetime.combine(self.week_start, time(23, 59, 59)),
        )

    def test_legacy_auto_closed_records_do_not_pollute_estimate(self):
        user = self._user("historialCA", weekly_hours=40)
        for weeks_back in (1, 2, 3):
            day = self.week_start - timedelta(days=7 * weeks_back)
            check_in = datetime.combine(day, time(9, 0))
            db.session.add(TimeRecord(
                user_id=user.id,
                date=day,
                check_in=check_in,
                check_out=datetime.combine(day, time(23, 59, 59)),
                notes="CA",
            ))
        db.session.commit()

        open_record = TimeRecord(
            user_id=user.id,
            date=self.week_start,
            check_in=datetime.combine(self.week_start, time(9, 0)),
        )
        db.session.add(open_record)
        db.session.commit()

        estimate = estimate_auto_close_time(open_record)

        # Sin patrón usable cae a horas_semanales/5 = 8h, nunca a ~15h.
        self.assertIsNotNone(estimate)
        self.assertLessEqual(estimate, datetime.combine(self.week_start, time(17, 10)))

    def test_partial_day_is_topped_up_with_missing_shift(self):
        user = self._user("parcial40", weekly_hours=40)
        self._full_history_weeks(user)

        # Lunes solo turno de mañana (4h de las 8h esperadas).
        self._record(user, self.week_start, start_hour=9, hours=4)

        result = autofill_week(self.week_start, app=self.app)
        user_result = result.user_results[0]

        monday_records = TimeRecord.query.filter_by(
            user_id=user.id, date=self.week_start
        ).order_by(TimeRecord.check_in).all()
        self.assertEqual(len(monday_records), 2)
        top_up = monday_records[1]
        self.assertEqual(top_up.notes, "AA")
        # Empieza tras la última salida (13:00) más un descanso con jitter.
        self.assertGreater(top_up.check_in, monday_records[0].check_out)

        week_records = TimeRecord.query.filter(
            TimeRecord.user_id == user.id,
            TimeRecord.date >= self.week_start,
            TimeRecord.date <= self.week_start + timedelta(days=6),
        ).all()
        week_seconds = sum(
            int((r.check_out - r.check_in).total_seconds()) for r in week_records
        )
        self.assertEqual(week_seconds, 40 * 3600)
        self.assertEqual(user_result.remaining_seconds, 0)

    def test_full_day_is_not_topped_up(self):
        user = self._user("completo40", weekly_hours=40)
        self._full_history_weeks(user)

        self._record(user, self.week_start, start_hour=9, hours=8)

        autofill_week(self.week_start, app=self.app)

        self.assertEqual(
            TimeRecord.query.filter_by(user_id=user.id, date=self.week_start).count(),
            1,
        )

    def test_running_twice_does_not_duplicate_records(self):
        user = self._user("idempotente", weekly_hours=8)

        first = autofill_week(self.week_start, app=self.app)
        second = autofill_week(self.week_start, app=self.app)

        self.assertGreater(first.created_records, 0)
        self.assertEqual(second.created_records, 0)
        self.assertEqual(self._worked_seconds(user), 8 * 3600)


if __name__ == "__main__":
    unittest.main()
