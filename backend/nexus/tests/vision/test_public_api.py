# tests/vision/test_public_api.py
def test_public_api_exports():
    from app.modules.vision import (
        analyze_session_proctoring,
        get_session_proctoring_analysis,
        ProctoringAnalysisRead,
        SessionProctoringAnalysis,
    )
    assert analyze_session_proctoring is not None
    assert get_session_proctoring_analysis is not None
    assert ProctoringAnalysisRead is not None
    assert SessionProctoringAnalysis is not None
