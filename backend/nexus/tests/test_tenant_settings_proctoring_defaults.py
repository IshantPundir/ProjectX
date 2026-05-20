from uuid import uuid4

from app.modules.tenant_settings import DEFAULT_TENANT_SETTINGS


def test_proctoring_defaults_on_lazy_default():
    s = DEFAULT_TENANT_SETTINGS(uuid4())
    assert s.proctoring_enabled is True
    assert s.proctoring_soft_violation_limit == 3
    assert s.proctoring_fullscreen_grace_seconds == 10
