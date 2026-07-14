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

    def _run(self):
        regularize_range(self.week_start, self.week_start + timedelta(days=6),
                         today=self.week_start + timedelta(days=14), dry_run=False)

    def _records(self, user):
        return TimeRecord.query.filter(
            TimeRecord.user_id == user.id,
            TimeRecord.date >= self.week_start,
            TimeRecord.date <= self.week_start + timedelta(days=6),
            TimeRecord.check_out.isnot(None),
        ).all()

    def _day_hours(self, user):
        return [(r.check_out - r.check_in).total_seconds() / 3600 for r in self._records(user)]

    def _week_hours(self, user):
        return sum(self._day_hours(user))

    # --- Cuadre semanal ---------------------------------------------------

    def test_ca_week_is_reduced_to_contract(self):
        # 20h con 3 días cerrados a 23:59 (CA) de ~14h -> semana inflada.
        user = self._user("inflado", 20)
        for off in (0, 1, 2):
            day = self.week_start + timedelta(days=off)
            ci = datetime.combine(day, time(9, 0))
            db.session.add(TimeRecord(
                user_id=user.id, date=day, check_in=ci,
                check_out=datetime.combine(day, time(23, 59, 59)), notes="CA"))
        db.session.commit()
        self.assertGreater(self._week_hours(user), 35)

        self._run()
        # Cuadra ~20h y ningún día pasa de 5h.
        self.assertGreaterEqual(self._week_hours(user), 18.5)
        self.assertLessEqual(self._week_hours(user), 21.5)
        self.assertLessEqual(max(self._day_hours(user)), 5.0 + 1e-6)

    # --- Tope aplicado también a días REALES ------------------------------

    def test_real_day_over_cap_is_trimmed_no_overtime(self):
        # 15h (tope 5h/día). Un día REAL de 8h -> se recorta a <=5h y NO se
        # genera ninguna hora extra (el cliente las gestiona en otra app).
        user = self._user("real8", 15)
        self._rec(user, day_offset=0, start_h=9, hours=8)  # lunes real 8h

        self._run()

        self.assertLessEqual(max(self._day_hours(user)), 5.0 + 1e-6)   # recortado al tope
        self.assertEqual(OvertimeAlert.query.count(), 0)               # sin horas extra
        self.assertGreaterEqual(self._week_hours(user), 14.0)          # cuadra ~15h
        self.assertLessEqual(self._week_hours(user), 16.0)

    def test_real_entry_time_is_preserved_when_trimmed(self):
        # La ENTRADA real se conserva aunque se recorte la salida al tope.
        user = self._user("entrada", 15)
        self._rec(user, day_offset=0, start_h=8, hours=9)  # entra a las 8:00

        self._run()

        lunes = TimeRecord.query.filter_by(
            user_id=user.id, date=self.week_start).first()
        self.assertEqual(lunes.check_in.time(), time(8, 0))            # entrada intacta
        self.assertLessEqual(
            (lunes.check_out - lunes.check_in).total_seconds() / 3600, 5.0 + 1e-6)
        self.assertIn("RGE", lunes.notes)                             # marcado entrada real

    # --- Nunca se generan alertas -----------------------------------------

    def test_no_alerts_are_ever_created(self):
        # Ni horas extra (día real largo) ni descuadre (entrada tardía sin salida).
        user = self._user("sinavisos", 20)
        self._rec(user, 0, 9, 12)  # día real enorme
        day = self.week_start + timedelta(days=3)
        db.session.add(TimeRecord(  # entrada tardía sin salida
            user_id=user.id, date=day,
            check_in=datetime.combine(day, time(22, 30)), check_out=None))
        db.session.commit()

        self._run()
        self.assertEqual(OvertimeAlert.query.count(), 0)

    def test_existing_alerts_are_cleared(self):
        # Alertas antiguas (lógica previa) se borran al regularizar la semana.
        user = self._user("limpia", 20)
        db.session.add(OvertimeAlert(
            user_id=user.id, week_start=self.week_start,
            date=self.week_start + timedelta(days=1),
            worked_seconds=36000, expected_seconds=18000,
            excess_seconds=18000, reviewed=False))
        self._rec(user, 0, 9, 4)
        db.session.commit()
        self.assertEqual(OvertimeAlert.query.count(), 1)

        self._run()
        self.assertEqual(OvertimeAlert.query.count(), 0)

    # --- Tope por contrato en días generados ------------------------------

    def test_generated_days_never_exceed_cap_10h(self):
        # 10h/sem, tope 3h/día -> ningún día >3h y repartido en >=4 días.
        user = self._user("cap10", 10)
        for off in (0, 1, 2):
            day = self.week_start + timedelta(days=off)
            db.session.add(TimeRecord(
                user_id=user.id, date=day, check_in=datetime.combine(day, time(9, 0)),
                check_out=datetime.combine(day, time(23, 59, 59)), notes="CA"))
        db.session.commit()

        self._run()
        h = self._day_hours(user)
        self.assertTrue(h)
        self.assertLessEqual(max(h), 3.0 + 1e-6)
        self.assertGreaterEqual(len(h), 4)
        self.assertGreaterEqual(self._week_hours(user), 9.0)
        self.assertLessEqual(self._week_hours(user), 11.0)

    def test_generated_days_never_exceed_cap_40h(self):
        # 40h/sem, tope 8h/día -> generados <=8h y semana <=40h (sin extra).
        user = self._user("cap40", 40)
        for off in (0, 1, 2):
            day = self.week_start + timedelta(days=off)
            db.session.add(TimeRecord(
                user_id=user.id, date=day, check_in=datetime.combine(day, time(8, 0)),
                check_out=datetime.combine(day, time(23, 59, 59)), notes="CA"))
        db.session.commit()

        self._run()
        h = self._day_hours(user)
        self.assertTrue(h)
        self.assertLessEqual(max(h), 8.0 + 1e-6)
        self.assertLessEqual(self._week_hours(user), 40.01)
        self.assertGreaterEqual(self._week_hours(user), 37.0)

    # --- Idempotencia ------------------------------------------------------

    def test_reruns_are_idempotent_and_keep_real_entries(self):
        user = self._user("estable", 20)
        for off in (0, 1, 2):
            day = self.week_start + timedelta(days=off)
            db.session.add(TimeRecord(
                user_id=user.id, date=day,
                check_in=datetime.combine(day, time(9, 0)),
                check_out=datetime.combine(day, time(23, 59, 59)), notes="CA"))
        db.session.commit()

        def snapshot():
            return sorted(
                (r.date.isoformat(), r.check_in.isoformat(),
                 r.check_out.isoformat(), r.notes)
                for r in TimeRecord.query.filter_by(user_id=user.id).all()
            )

        self._run()
        snap1 = snapshot()
        # Entradas reales conservadas y marcadas RGE.
        for off in (0, 1, 2):
            day = self.week_start + timedelta(days=off)
            rec = TimeRecord.query.filter_by(user_id=user.id, date=day).first()
            self.assertEqual(rec.check_in.time(), time(9, 0))
            self.assertIn("RGE", rec.notes)

        self._run()
        self.assertEqual(snapshot(), snap1)
        self._run()
        self.assertEqual(snapshot(), snap1)

    def test_dry_run_does_not_persist(self):
        user = self._user("seco", 15)
        self._rec(user, 0, 9, 8)
        regularize_range(self.week_start, self.week_start + timedelta(days=6),
                         today=self.week_start + timedelta(days=14), dry_run=True)
        # Sin persistir: el día real de 8h sigue intacto y no se recortó.
        recs = self._records(user)
        self.assertEqual(len(recs), 1)
        self.assertEqual((recs[0].check_out - recs[0].check_in).total_seconds() / 3600, 8)


if __name__ == "__main__":
    unittest.main()
