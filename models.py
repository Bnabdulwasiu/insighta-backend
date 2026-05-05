from sqlalchemy import (Column, String, Float, Integer,
                         DateTime, Boolean, Index)
import uuid6
from datetime import datetime, timezone
from database import Base
from sqlalchemy.dialects.postgresql import UUID

class Profile(Base):
    __tablename__ = "profiles"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid6.uuid7)
    name = Column(String, unique=True, index=True)
    gender = Column(String, nullable=True, index=True)
    gender_probability = Column(Float, nullable=True)
    age = Column(Integer, nullable=True, index=True)
    age_group = Column(String, nullable=True, index=True)
    country_id = Column(String(2), nullable=True, index=True)
    country_name = Column(String, nullable=True)
    country_probability = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        # Composite index: covers queries filtering on both gender AND country together
        Index("ix_profiles_gender_country_age", "gender", "country_id", "age")
    )


class User(Base):
    __tablename__ = "users"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid6.uuid7)
    github_id = Column(String, unique=True, nullable=False)
    username = Column(String, nullable=False)
    email = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)
    role = Column(String, default="analyst")          # "admin" or "analyst"
    is_active = Column(Boolean, default=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid6.uuid7)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    token = Column(String, unique=True, nullable=False)
    is_revoked = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=False)