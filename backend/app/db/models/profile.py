from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.db.models.common import EntityBase


class UserProfile(EntityBase, table=True):
    __tablename__ = "user_profiles"

    display_name: str = Field(default="Local User", min_length=1, max_length=120)
    locale: str = Field(default="zh-CN", min_length=2, max_length=16)
    timezone: str = Field(default="Asia/Hong_Kong", min_length=1, max_length=64)
    preferences: dict = Field(default_factory=dict, sa_type=JSONB, nullable=False)
    memory_enabled: bool = Field(default=True, nullable=False)
