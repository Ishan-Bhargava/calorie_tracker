"""SQLAlchemy ORM models."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


@dataclass(frozen=True)
class UserInfo:
    """Plain, detached snapshot of a user (safe to keep in session/pass around)."""

    id: int
    username: str
    email: str
    calorie_target: int
    protein_target_g: Optional[int]
    carbs_target_g: Optional[int]
    fat_target_g: Optional[int]
    timezone: str


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    calorie_target: Mapped[int] = mapped_column(Integer, nullable=False, default=2200)
    protein_target_g: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    carbs_target_g: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fat_target_g: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    food_logs: Mapped[list["FoodLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    chat_messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    def to_info(self) -> UserInfo:
        return UserInfo(
            id=self.id,
            username=self.username,
            email=self.email,
            calorie_target=self.calorie_target,
            protein_target_g=self.protein_target_g,
            carbs_target_g=self.carbs_target_g,
            fat_target_g=self.fat_target_g,
            timezone=self.timezone,
        )


# Case-insensitive unique usernames ("John" and "john" cannot both exist).
Index("uq_users_username_lower", func.lower(User.username), unique=True)


class FoodLog(Base):
    __tablename__ = "food_logs"
    __table_args__ = (Index("ix_food_logs_user_date", "user_id", "date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    meal_type: Mapped[str] = mapped_column(String(16), nullable=False, default="Other")
    food_name: Mapped[str] = mapped_column(String(120), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    unit: Mapped[str] = mapped_column(String(30), nullable=False, default="serving")
    calories: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    protein_g: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    carbs_g: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fat_g: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # "estimated" (AI estimate) or "user" (number given by the user / entered manually)
    calorie_source: Mapped[str] = mapped_column(String(12), nullable=False, default="estimated")
    notes: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="food_logs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "date": self.date,
            "meal_type": self.meal_type,
            "food_name": self.food_name,
            "quantity": self.quantity,
            "unit": self.unit,
            "calories": self.calories,
            "protein_g": self.protein_g,
            "carbs_g": self.carbs_g,
            "fat_g": self.fat_g,
            "calorie_source": self.calorie_source,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(12), nullable=False)  # "user" | "assistant"
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    user: Mapped[User] = relationship(back_populates="chat_messages")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "message": self.message,
            "created_at": self.created_at,
        }
