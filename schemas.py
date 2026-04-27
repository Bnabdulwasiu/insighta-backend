from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class ProfileSchema(BaseModel):
    id: str
    name: str
    gender: Optional[str]
    gender_probability: Optional[float]
    age: Optional[int]
    age_group: Optional[str]
    country_id: Optional[str]
    country_name: Optional[str]
    country_probability: Optional[float]
    created_at: Optional[datetime]


    class Config:
        from_attributes = True

class ProfileListResponse(BaseModel):
    status: str = "success"
    page: int
    limit: int
    total: int
    data: List[ProfileSchema]

class CreateProfileRequest(BaseModel):
    name: str

class RefreshRequest(BaseModel):
    refresh_token: str