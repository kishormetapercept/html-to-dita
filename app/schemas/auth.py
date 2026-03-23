from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


class HtmlToDitaRequest(BaseModel):
    userId: str = Field(min_length=1)


class TagItem(BaseModel):
    key: str = Field(min_length=1)
    value: str
