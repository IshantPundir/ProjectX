"""OTP helpers — generation, hashing, verification."""
import re

from app.modules.session.otp import generate_code, hash_code, verify_code


def test_generate_code_is_6_digit_numeric():
    for _ in range(50):
        code = generate_code()
        assert re.fullmatch(r"\d{6}", code)


def test_generate_code_has_entropy():
    seen = {generate_code() for _ in range(200)}
    # With ~1M search space, 200 samples should give >150 unique codes with overwhelming probability.
    assert len(seen) > 150


def test_hash_and_verify_round_trip():
    code = "123456"
    h = hash_code(code)
    assert h != code
    assert verify_code(code, h) is True
    assert verify_code("000000", h) is False


def test_hash_produces_consistent_output():
    """Same code hashed twice yields identical hash (no per-call salt).

    This enables fast server-side check against stored hash without per-user pepper."""
    assert hash_code("111111") == hash_code("111111")


def test_hash_differs_by_code():
    assert hash_code("123456") != hash_code("654321")
