from sqlalchemy import Column, String, Float, Integer, DateTime
import uuid6
from datetime import datetime, timezone
from database import Base
from sqlalchemy.dialects.postgresql import UUID

class Profile(Base):
    __tablename__ = "profiles"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid6.uuid7)
    name = Column(String, unique=True, index=True)
    gender = Column(String, nullable=True)
    gender_probability = Column(Float, nullable=True)
    age = Column(Integer, nullable=True)
    age_group = Column(String, nullable=True)
    country_id = Column(String(2), nullable=True)
    country_name = Column(String, nullable=True)
    country_probability = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
