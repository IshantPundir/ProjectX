from app.modules.reporting.scoring.scale import to_ten


def test_to_ten_rounds_to_one_decimal():
    assert to_ten(81) == 8.1
    assert to_ten(65) == 6.5
    assert to_ten(100) == 10.0
    assert to_ten(0) == 0.0
    assert to_ten(35) == 3.5


def test_to_ten_passthrough_none():
    assert to_ten(None) is None


def test_to_ten_accepts_float():
    assert to_ten(72.0) == 7.2
