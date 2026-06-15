import unittest
from datetime import date

from flask import Flask

from models.database import db
from models.models import User
from services.weekly_hours import (
    apply_weekly_hours_change,
    weekly_hours_for_date,
    weekly_hours_for_week,
)


class WeeklyHoursHistoryTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        self.app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        self.app.config["TESTING"] = True
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def _user(self):
        user = User(
            username="jornada",
            full_name="Cambio Jornada",
            email="jornada@example.com",
            is_admin=False,
            is_active=True,
            weekly_hours=40,
            hire_date=date(2026, 1, 1),
        )
        user.set_password("secret")
        db.session.add(user)
        db.session.commit()
        return user

    def test_change_closes_previous_period_and_opens_new_one(self):
        user = self._user()

        apply_weekly_hours_change(
            user,
            15,
            effective_date=date(2026, 4, 1),
            previous_weekly_hours=40,
        )
        db.session.commit()

        periods = user.weekly_hours_periods
        self.assertEqual(len(periods), 2)
        self.assertEqual(periods[0].weekly_hours, 40)
        self.assertEqual(periods[0].start_date, date(2026, 1, 1))
        self.assertEqual(periods[0].end_date, date(2026, 3, 31))
        self.assertEqual(periods[1].weekly_hours, 15)
        self.assertEqual(periods[1].start_date, date(2026, 4, 1))
        self.assertIsNone(periods[1].end_date)

    def test_weekly_rule_uses_hours_active_at_week_end(self):
        user = self._user()
        apply_weekly_hours_change(
            user,
            15,
            effective_date=date(2026, 4, 1),
            previous_weekly_hours=40,
        )
        db.session.commit()

        self.assertEqual(weekly_hours_for_date(user, date(2026, 3, 31)), 40)
        self.assertEqual(weekly_hours_for_date(user, date(2026, 4, 1)), 15)
        self.assertEqual(weekly_hours_for_week(user, date(2026, 3, 30)), 15)
        self.assertEqual(weekly_hours_for_week(user, date(2026, 3, 23)), 40)


if __name__ == "__main__":
    unittest.main()
