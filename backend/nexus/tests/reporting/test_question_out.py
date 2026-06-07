from app.modules.reporting.scoring.status import badge_for_question


def test_badge_passed_for_solid():
    assert badge_for_question(level="solid", provenance="asked_directly", knockout=False)[0] == "passed"


def test_badge_failed_required_for_absent_must_have():
    assert badge_for_question(level="absent", provenance="probed_absent", knockout=True)[0] == "failed_required"


def test_badge_not_attempted_for_not_reached():
    assert badge_for_question(level="not_reached", provenance="not_reached", knockout=False)[0] == "not_attempted"
