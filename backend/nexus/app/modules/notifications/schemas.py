from pydantic import BaseModel


class EmailMessage(BaseModel):
    to: str
    subject: str
    body_html: str
    body_text: str = ""


class SMSMessage(BaseModel):
    to: str  # E.164 format
    body: str
