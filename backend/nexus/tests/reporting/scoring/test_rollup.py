from app.modules.reporting.scoring.rollup import roll_up_signal, pick_dedicated_question


def test_dedicated_thin_plus_strong_cross_credit_lifts_one_to_solid():
    r = roll_up_signal(signal="s", dedicated_level="thin", dedicated_outcome="asked",
                       cross_credit_level="strong")
    assert r.level == "solid"          # +1 only, never thin→strong
    assert r.cross_credit_applied is True
    assert "thin" in r.level_basis and "solid" in r.level_basis


def test_dedicated_thin_no_cross_credit_stays_thin():
    r = roll_up_signal(signal="s", dedicated_level="thin", dedicated_outcome="asked",
                       cross_credit_level=None)
    assert r.level == "thin" and r.cross_credit_applied is False


def test_dedicated_absent_disclaim_not_lifted():
    r = roll_up_signal(signal="s", dedicated_level="absent", dedicated_outcome="asked",
                       cross_credit_level="strong")
    assert r.level == "absent" and r.cross_credit_applied is False


def test_dedicated_not_reached_cross_credit_authoritative():
    r = roll_up_signal(signal="s", dedicated_level=None, dedicated_outcome="not_reached",
                       cross_credit_level="solid")
    assert r.level == "solid" and r.cross_credit_applied is True


def test_no_dedicated_and_no_cross_credit_is_not_reached():
    r = roll_up_signal(signal="s", dedicated_level=None, dedicated_outcome=None,
                       cross_credit_level=None)
    assert r.level == "not_reached"


def test_solid_dedicated_plus_strong_cross_credit_lifts_to_strong():
    r = roll_up_signal(signal="s", dedicated_level="solid", dedicated_outcome="asked",
                       cross_credit_level="strong")
    assert r.level == "strong"


def test_pick_dedicated_prefers_asked_then_lowest_position():
    questions = [
        {"id": "qA", "primary_signal": "s", "position": 7},
        {"id": "qB", "primary_signal": "s", "position": 0},
    ]
    outcomes = {"qA": "not_reached", "qB": "asked"}
    assert pick_dedicated_question("s", questions, outcomes)["id"] == "qB"


def test_pick_dedicated_two_asked_lowest_position_wins():
    questions = [
        {"id": "qA", "primary_signal": "s", "position": 3},
        {"id": "qB", "primary_signal": "s", "position": 1},
    ]
    outcomes = {"qA": "asked", "qB": "asked"}
    assert pick_dedicated_question("s", questions, outcomes)["id"] == "qB"
