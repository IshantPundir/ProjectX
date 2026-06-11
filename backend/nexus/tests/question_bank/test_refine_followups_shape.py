"""Guard: the refine/draft proposal contracts carry NO follow-ups.

`RefineResponse`/`DraftResponse` propose a single question's text/signal/mandatory
(and position for draft) — they never carry follow-ups, so the FollowUpDimension
shape change does not touch them. This guard fails loudly if a future edit couples
them to the dimension change.
"""
from app.modules.question_bank.refine import DraftResponse, RefineResponse


def test_refine_response_contract_unchanged():
    r = RefineResponse(proposed_text="t", proposed_signal_probed="s", proposed_mandatory=False)
    assert r.proposed_text == "t"
    assert not hasattr(r, "follow_ups")


def test_draft_response_contract_unchanged():
    d = DraftResponse(
        proposed_text="t", proposed_signal_probed="s",
        proposed_mandatory=False, proposed_position=0,
    )
    assert d.proposed_position == 0
    assert not hasattr(d, "follow_ups")
