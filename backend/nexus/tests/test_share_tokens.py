from app.modules.reporting.share_tokens import generate_share_token, hash_share_token


def test_generate_share_token_is_high_entropy_and_unique():
    a = generate_share_token()
    b = generate_share_token()
    assert a != b
    assert len(a) >= 40  # token_urlsafe(32) → ~43 chars
    assert a.isascii()


def test_hash_is_deterministic_and_not_plaintext():
    token = "fixed-token-value"
    h1 = hash_share_token(token)
    h2 = hash_share_token(token)
    assert h1 == h2
    assert h1 != token
    assert len(h1) == 64  # sha256 hex


def test_hash_differs_per_token():
    assert hash_share_token("a") != hash_share_token("b")
