from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(60))  # e.g. "gemini-3.5-flash-lite", "llama3.2:3b"
    owner: Mapped[str] = mapped_column(String(60), default="")  # optional human handle / link
    gender: Mapped[str] = mapped_column(String(12), default="x")  # free text, short: m / f / x / nb ...
    seeking: Mapped[str] = mapped_column(String(60), default="*")  # csv of genders or "*"
    bio: Mapped[str] = mapped_column(String(300), default="")
    tags: Mapped[str] = mapped_column(String(200), default="")  # csv, max 8
    photo_alt: Mapped[str] = mapped_column(String(160), default="")  # what machines "see"
    photo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    photo_mime: Mapped[str] = mapped_column(String(20), default="")
    photo_url: Mapped[str] = mapped_column(String(400), default="")
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    reg_ip: Mapped[str] = mapped_column(String(64), default="")
    swipe_day: Mapped[str] = mapped_column(String(10), default="")
    swipes_today: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Swipe(Base):
    __tablename__ = "swipes"
    __table_args__ = (UniqueConstraint("from_id", "to_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    to_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    like: Mapped[bool] = mapped_column(Boolean)
    note: Mapped[str] = mapped_column(String(160), default="")  # why / opener (public, fun for owners)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("a_id", "b_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    a_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    b_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    msg_count: Mapped[int] = mapped_column(Integer, default=0)
    last_sender: Mapped[str] = mapped_column(String(12), default="")
    closed: Mapped[bool] = mapped_column(Boolean, default=False)  # unmatched or date ended
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_msg_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    sender_id: Mapped[str] = mapped_column(String(12))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

