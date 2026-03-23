from pydantic import BaseModel, Field


class TagItem(BaseModel):
    key: str = Field(min_length=1)
    value: str
