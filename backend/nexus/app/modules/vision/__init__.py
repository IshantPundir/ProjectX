"""Vision proctoring (server plane) — public API.

Heavy deps (onnxruntime/cv2/uniface) are imported lazily inside the actor/estimator, so
importing this package in the lean nexus API process is safe.
"""
from app.modules.vision.actors import analyze_session_proctoring
from app.modules.vision.models import SessionProctoringAnalysis
from app.modules.vision.schemas import ProctoringAnalysisRead
from app.modules.vision.service import get_session_proctoring_analysis

__all__ = [
    "ProctoringAnalysisRead",
    "SessionProctoringAnalysis",
    "analyze_session_proctoring",
    "get_session_proctoring_analysis",
]
