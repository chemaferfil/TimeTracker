import unittest
from datetime import date, datetime, time, timedelta

from flask import Flask

from models.database import db
from models.models import OvertimeAlert, TimeRecord, User
from tasks.regularize import regularize_range


class RegularizeTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        self.app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        self.app.config["TESTING"] = True
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.week_start = date(2026, 5, 4)  # lunes

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def _user(self, username, weekly_hours):
        user = User(
            username=username, full_name=username.title(),
            email=f"{username}@example.com", is_admin=False, is_active=True,
            weekly_hours=weekly_hours, categoria="Reparto",
        )
        user.set_password("secret")
        db.session.add(user)
        db.session.commit()
        return user

    def _rec(self, user, day_offset, start_h, hours, notes=""):
        day = self.week_start + timedelta(days=day_offset)
        ci = datetime.combine(day, time(start_h, 0))
        r = TimeRecord(user_id=user.id, date=day, check_in=ci,
                       check_out=ci + timedelta(hours=hours), notes=notes)
        db.session.add(r)
        db.session.commit()
        return r

    def _week_hours(self, user):
        rr = TimeRecord.query.filter(
            TimeRecord.user_id == user.id,
            TimeRecord.date >= self.week_start,
            TimeRecord.date <= self.week_start + timedelta(days=6),
            TimeRecord.check_out.isnot(None),
        ).all()
        return sum((r.check_out - r.check_in).total_seconds() for r in rr) / 3600

    def test_ca_week_is_reduced_to_contract(self):
        # 20h con 3 días cerrados a 23:59 (CA) de ~14h cada uno -> semana inflada
        user = self._user("inflado", 20)
        for off in (0, 1, 2):
            day = self.week_start + timedelta(days=off)
            ci = datetime.combine(day, time(9, 0))
            db.session.add(TimeRecord(
                user_id=user.id, date=day, check_in=ci,
                check_out=datetime.combine(day, time(23, 59, 59)), notes="CA"))
        db.session.commit()
        self.assertGreater(self._week_hours(user), 35)

        regularize_range(self.week_start, self.week_start + timedelta(days=6),
                         today=self.week_start + timedelta(days=14), dry_run=False)

        # tras regularizar, la semana queda cerca del contrato (natural ±)
        self.assertGreaterEqual(self._week_hours(user), 17)
        self.assertLessEqual(self._week_hours(user), 23)

    def test_real_complete_day_is_kept_and_overtime_flagged(self):
        # 15h (esperado 5h/día). Un día REAL de 8h -> horas extra + se conserva.
        user = self._user("extra", 15)
        real = self._rec(user, day_offset=0, start_h=9, hours=8)  # lunes real 8h

        regularize_range(self.week_start, self.week_start + timedelta(days=6),
                         today=self.week_start + timedelta(days=14), dry_run=False)

        kept = db.session.get(TimeRecord, real.id)
        self.assertIsNotNone(kept)  # el día real no se borra
        self.assertEqual((kept.check_out - kept.check_in).total_seconds() / 3600, 8)

        alert = OvertimeAlert.query.filter_by(user_id=user.id).first()
        self.assertIsNotNone(alert)
        self.assertEqual(alert.date, self.week_start)
        # 8h trabajadas vs 5h esperadas -> +3h de exceso
        self.assertAlmostEqual(alert.excess_seconds / 3600, 3.0, delta=0.05)

    def test_overtime_alert_is_idempotent(self):
        user = self._user("extra2", 15)
        self._rec(user, 0, 9, 8)
        rng = (self.week_start, self.week_start + timedelta(days=6))
        regularize_range(*rng, today=self.week_start + timedelta(days=14), dry_run=False)
        first = OvertimeAlert.query.count()
        regularize_range(*rng, today=self.week_start + timedelta(days=14), dry_run=False)
        self.assertEqual(OvertimeAlert.query.count(), first)

    def test_late_entry_without_exit_creates_shortfall_alert(self):
        # 20h. Única actividad: entrada real a las 22:30 sin salida -> la salida
        # se capa a las 23:59 y la semana queda muy por debajo del objetivo.
        user = self._user("tardio", 20)
        day = self.week_start + timedelta(days=3)
        db.session.add(TimeRecord(
            user_id=user.id, date=day,
            check_in=datetime.combine(day, time(22, 30)), check_out=None))
        db.session.commit()

        regularize_range(self.week_start, self.week_start + timedelta(days=6),
                         today=self.week_start + timedelta(days=14), dry_run=False)

        # La entrada real tardía se conserva y la salida no pasa del mismo día
        late = TimeRecord.query.filter_by(user_id=user.id, date=day).first()
        self.assertEqual(late.check_in.time(), time(22, 30))
        self.assertLessEqual(late.check_out, datetime.combine(day, time(23, 59, 59)))

        # El día capado deja la semana ~3.5h corta -> aviso de descuadre
        alerts = OvertimeAlert.query.filter_by(user_id=user.id).all()
        shortfalls = [a for a in alerts if a.excess_seconds < 0]
        self.assertEqual(len(shortfalls), 1)
        self.assertEqual(shortfalls[0].week_start, self.week_start)

    def test_shortfall_alert_is_idempotent(self):
        user = self._user("tardio2", 20)
        day = self.week_start + timedelta(days=3)
        db.session.add(TimeRecord(
            user_id=user.id, date=day,
            check_in=datetime.combine(day, time(22, 30)), check_out=None))
        db.session.commit()
        rng = (self.week_start, self.week_start + timedelta(days=6))
        regularize_range(*rng, today=self.week_start + timedelta(days=14), dry_run=False)
        first = OvertimeAlert.query.count()
        regularize_range(*rng, today=self.week_start + timedelta(days=14), dry_run=False)
        self.assertEqual(OvertimeAlert.query.count(), first)

    def test_dry_run_does_not_persist(self):
        user = self._user("extra3", 15)
        self._rec(user, 0, 9, 8)
        regularize_range(self.week_start, self.week_start + timedelta(days=6),
                         today=self.week_start + timedelta(days=14), dry_run=True)
        self.assertEqual(OvertimeAlert.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
