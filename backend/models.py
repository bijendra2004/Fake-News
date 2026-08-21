from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from .crypto import EncryptedText


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(EncryptedText(), unique=True, nullable=False, index=True)
    phone: Mapped[Optional[str]] = mapped_column(EncryptedText(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    history: Mapped[list[SearchHistory]] = relationship(back_populates="user")


class SearchHistory(Base):
    __tablename__ = "search_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    prediction_label: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped[Optional[User]] = relationship(back_populates="history")


class FreeUsageTracking(Base):
    __tablename__ = "free_usage_tracking"
    __table_args__ = (
        UniqueConstraint("device_fingerprint", "ip_address", name="uq_device_ip_usage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    abuse_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    captcha_required_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    first_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class SourceCredibility(Base):
    __tablename__ = "source_credibility"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    credibility_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class OTPChallenge(Base):
    __tablename__ = "otp_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(EncryptedText(), nullable=False, index=True)
    otp_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RefreshTokenRecord(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(EncryptedText(), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class UsageRow:
    def __init__(self, row: FreeUsageTracking):
        self.row = row

    @property
    def usage_count(self) -> int:
        return self.row.usage_count


DB_SESSION_EXPIRES_IN_SECONDS = 60 * 60 * 24


def init_db(engine) -> None:
    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "sqlite":
        inspector = inspect(engine)
        columns = {column["name"] for column in inspector.get_columns("free_usage_tracking")}
        # NOTE: These text() calls are static DDL migration strings with no user
        # input — safe from SQL injection. They exist only for backwards-compatible
        # schema migration on existing SQLite databases.
        with engine.begin() as connection:
            if "abuse_count" not in columns:
                connection.execute(text("ALTER TABLE free_usage_tracking ADD COLUMN abuse_count INTEGER NOT NULL DEFAULT 0"))
            if "captcha_required_until" not in columns:
                connection.execute(text("ALTER TABLE free_usage_tracking ADD COLUMN captcha_required_until TIMESTAMPTZ"))


def get_or_create_usage_row(db: Session, device_fingerprint: str, ip_address: str) -> FreeUsageTracking:
    usage_row = db.execute(
        select(FreeUsageTracking).where(
            FreeUsageTracking.device_fingerprint == device_fingerprint,
            FreeUsageTracking.ip_address == ip_address,
        )
    ).scalar_one_or_none()
    if usage_row is None:
        usage_row = FreeUsageTracking(device_fingerprint=device_fingerprint, ip_address=ip_address, usage_count=0)
        db.add(usage_row)
        db.commit()
        db.refresh(usage_row)
    return usage_row


def seed_usage_if_missing(db: Session, device_fingerprint: str, ip_address: str) -> FreeUsageTracking:
    return get_or_create_usage_row(db, device_fingerprint=device_fingerprint, ip_address=ip_address)


def get_or_create_user(db: Session, email: str) -> User:
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(email=email)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def increment_usage(db: Session, usage_row: FreeUsageTracking) -> None:
    usage_row.usage_count += 1
    usage_row.last_used_at = datetime.now(timezone.utc)
    db.add(usage_row)
    db.commit()
    db.refresh(usage_row)


def register_abuse(db: Session, usage_row: FreeUsageTracking) -> FreeUsageTracking:
    usage_row.abuse_count += 1
    usage_row.last_used_at = datetime.now(timezone.utc)
    db.add(usage_row)
    db.commit()
    db.refresh(usage_row)
    return usage_row


def set_captcha_required_until(db: Session, usage_row: FreeUsageTracking, until: datetime | None) -> FreeUsageTracking:
    usage_row.captcha_required_until = until
    usage_row.last_used_at = datetime.now(timezone.utc)
    db.add(usage_row)
    db.commit()
    db.refresh(usage_row)
    return usage_row
