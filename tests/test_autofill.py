import unittest
from datetime import date, datetime, time, timedelta

from flask import Flask

from models.database import db
from models.models import EmployeeStatus, TimeRecord, User
from tasks.autofill import autofill_week


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

    def test_running_twice_does_not_duplicate_records(self):
        user = self._user("idempotente", weekly_hours=8)

        first = autofill_week(self.week_start, app=self.app)
        second = autofill_week(self.week_start, app=self.app)

        self.assertGreater(first.created_records, 0)
        self.assertEqual(second.created_records, 0)
        self.assertEqual(self._worked_seconds(user), 8 * 3600)


if __name__ == "__main__":
    unittest.main()
