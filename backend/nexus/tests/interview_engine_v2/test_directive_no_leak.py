"""No-rubric-leak: a Directive may not carry evaluation/rubric text in say/compose_hint."""

import pytest
from pydantic import ValidationError

from app.modules.interview_engine_v2.directive import (
    Directive,
    DirectiveAct,
    FORBIDDEN_RUBRIC_TOKENS,
)


def test_clean_directive_passes():
    Directive(id="d-1", turn_ref="t-1", act=DirectiveAct.ASK,
              say="Walk me through a deploy that went wrong.")


@pytest.mark.parametrize("leak", [
    "We're looking for evidence of idempotency.",
    "Listen for the red flags here.",
    "Score against the rubric: excellent / meets_bar / below_bar.",
    "positive_evidence: names a real tool.",
])
def test_leak_in_say_rejected(leak):
    with pytest.raises(ValidationError):
        Directive(id="d-2", turn_ref="t-1", act=DirectiveAct.ANSWER_META, say=leak)


def test_leak_in_compose_hint_rejected():
    with pytest.raises(ValidationError):
        Directive(id="d-3", turn_ref="t-1", act=DirectiveAct.HOLD, say=None,
                  compose_hint="hint at the red_flags we track")


def test_forbidden_tokens_are_lowercased_substrings():
    # negative control: every token must trip the check regardless of case.
    for tok in FORBIDDEN_RUBRIC_TOKENS:
        with pytest.raises(ValidationError):
            Directive(id="d-x", turn_ref="t-1", act=DirectiveAct.ANSWER_META,
                      say=f"... {tok.upper()} ...")
