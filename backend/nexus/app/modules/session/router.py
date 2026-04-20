"""Session routers — stubs retained so app/main.py imports succeed.

Full candidate-facing + recruiter-read endpoints land in Task 3C.1.17.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
candidate_router = APIRouter(prefix="/api/candidate-session", tags=["candidate_session"])
