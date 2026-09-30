import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    admin = "admin"
    doctor = "doctor"


class AppointmentStatus(str, enum.Enum):
    scheduled = "scheduled"
    completed = "completed"
    cancelled = "cancelled"


class AuditMixin:
    """created_at/updated_at are set by the DB; created_by/updated_by are set
    by services.py from the JWT user, since the DB layer has no request context."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    created_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)


class Doctor(Base, AuditMixin):
    __tablename__ = "doctors"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    # unique=True already creates a DB-level unique index/constraint (Level 13)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(10))
    specialization: Mapped[str] = mapped_column(String(100), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    patients: Mapped[list["Patient"]] = relationship(back_populates="doctor")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="doctor")
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), unique=True, nullable=True, index=True
    )
    user: Mapped["User"] = relationship(back_populates="doctor_profile")


class Patient(Base, AuditMixin):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    age: Mapped[int] = mapped_column(Integer, index=True)
    phone: Mapped[str] = mapped_column(String(10))
    # ondelete="RESTRICT": explicit DB-level FK behavior (Level 13) - a doctor
    # with patients cannot be hard-deleted (soft delete is used instead anyway)
    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("doctors.id", ondelete="RESTRICT"), index=True
    )

    doctor: Mapped[Doctor] = relationship(back_populates="patients")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="patient")


class Appointment(Base, AuditMixin):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id", ondelete="RESTRICT"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"), index=True)
    appointment_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus), default=AppointmentStatus.scheduled, index=True
    )

    doctor: Mapped[Doctor] = relationship(back_populates="appointments")
    patient: Mapped[Patient] = relationship(back_populates="appointments")

    # Level 14: composite index speeds up the overlap check and the
    # GET /doctors/{id}/appointments listing, which both filter on these two columns together
    __table_args__ = (Index("ix_appointments_doctor_date", "doctor_id", "appointment_date"),)


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    cancelled = "cancelled"


class PaymentMode(str, enum.Enum):
    cash = "cash"
    card = "card"
    upi = "upi"


class Billing(Base, AuditMixin):
    __tablename__ = "billings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"), index=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id", ondelete="RESTRICT"), index=True)
    # nullable + unique: optional, but when present a given appointment can
    # only ever be billed once (Level 27's "prevent duplicate billing")
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="RESTRICT"), unique=True, nullable=True, index=True
    )
    consultation_fee: Mapped[float] = mapped_column(Numeric(10, 2))
    additional_charges: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    # total_amount is server-computed (services.py), never taken from the client
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2))
    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus), default=PaymentStatus.pending, index=True
    )
    payment_mode: Mapped[PaymentMode] = mapped_column(Enum(PaymentMode))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    patient: Mapped[Patient] = relationship()
    doctor: Mapped[Doctor] = relationship()
    appointment: Mapped[Appointment | None] = relationship()

    # Level 28: date-range revenue reports filter/group by (doctor_id, created_at)
    # Level 29: DB-level guards, in addition to the Pydantic Field(ge=0) checks -
    # these still hold even if a row is ever written outside the API.
    __table_args__ = (
        Index("ix_billings_doctor_created", "doctor_id", "created_at"),
        CheckConstraint("consultation_fee >= 0", name="ck_billing_consultation_fee_nonneg"),
        CheckConstraint("additional_charges >= 0", name="ck_billing_additional_charges_nonneg"),
        CheckConstraint("total_amount >= 0", name="ck_billing_total_amount_nonneg"),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.doctor, index=True)

    doctor_profile: Mapped[Doctor | None] = relationship(back_populates="user")

    @property
    def doctor_id(self) -> int | None:
        """Convenience accessor so UserOut can read doctor_id via from_attributes."""
        return self.doctor_profile.id if self.doctor_profile else None
