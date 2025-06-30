from .database import db
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    weekly_hours = db.Column(db.Integer, nullable=False, default=0)
    categoria = db.Column(
        db.Enum(
            "Cocina", "Delivery", "Reparto", "Sala",
            name="category_enum"
        ),
        nullable=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    time_records = db.relationship(
        "TimeRecord",
        backref="user",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="TimeRecord.user_id"
    )

    statuses = db.relationship(
        "EmployeeStatus",
        backref="user",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"

class TimeRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    check_in = db.Column(db.DateTime, nullable=True)
    check_out = db.Column(db.DateTime, nullable=True)
    date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    modified_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<TimeRecord {self.id}-U{self.user_id}>"

class EmployeeStatus(db.Model):
    __tablename__ = "employee_status"
    __table_args__ = (
        db.UniqueConstraint("user_id", "date", name="uix_employee_date"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False
    )
    date = db.Column(db.Date, nullable=False)
    status = db.Column(
        db.Enum(
            "Trabajado", "Baja", "Ausente", "Vacaciones",
            name="status_enum"
        ),
        nullable=False,
        default=""
    )
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    def __repr__(self):
        return (
            f"<EmployeeStatus {self.id}-U{self.user_id} "
            f"{self.date} {self.status}>"
        )
