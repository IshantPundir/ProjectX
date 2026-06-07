from app.modules.reporting.scoring.constants import LEVEL_POINTS, level_score


def test_level_points_ordering():
    assert LEVEL_POINTS["strong"] > LEVEL_POINTS["solid"] > LEVEL_POINTS["thin"]
    assert LEVEL_POINTS["thin"] > LEVEL_POINTS["absent"]


def test_absent_and_not_reached_share_the_floor():
    # Uniform low band: never-asked scores exactly as asked-and-absent.
    assert LEVEL_POINTS["absent"] == LEVEL_POINTS["not_reached"]
    assert level_score("absent") == level_score("not_reached")


def test_level_score_passthrough():
    assert level_score("strong") == 100
    assert level_score("not_reached") == LEVEL_POINTS["not_reached"]
