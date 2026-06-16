import pytest
from pydantic import ValidationError

from app.config import Settings


def test_share_secret_required_outside_test_env():
    with pytest.raises(ValidationError):
        Settings(environment="production", candidate_jwt_secret="x",
                 recording_share_hmac_secret="")


def test_share_secret_optional_in_test_env():
    s = Settings(environment="test", recording_share_hmac_secret="")
    assert s.recording_share_hmac_secret == ""
    assert s.recording_share_ttl_days == 365
