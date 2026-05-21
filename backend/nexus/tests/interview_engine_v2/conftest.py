import pytest

from app.modules.interview_engine_v2.directive import Directive, DirectiveAct


@pytest.fixture
def make_directive():
    """Factory: make_directive(id, turn_ref, act=ASK, say='...', **kw)."""
    def _make(id, turn_ref, *, act=DirectiveAct.ASK, say="Tell me about your last project.", **kw):
        return Directive(id=id, turn_ref=turn_ref, act=act, say=say, **kw)
    return _make
