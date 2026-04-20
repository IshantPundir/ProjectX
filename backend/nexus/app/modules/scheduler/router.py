"""Scheduler router — stub retained so app/main.py imports succeed.

Full invite endpoints land in Task 3C.1.18.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])
