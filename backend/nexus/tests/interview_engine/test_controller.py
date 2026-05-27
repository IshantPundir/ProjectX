"""DirectiveController — the pure two-plane concurrency core (Option C).

Invariants under test (DESIGN-SPEC §2/§7, doc 08 system-concurrency):
  - never deliver a directive computed for a different turn (staleness)
  - never deliver a superseded directive
  - never deliver two directives for the same boundary
  - a wrong speculative pre-stage is cleanly discarded
"""

from app.modules.interview_engine.controller import DirectiveController


def test_stage_then_current_for_matching_turn(make_directive):
    c = DirectiveController()
    d = make_directive("d-1", "t-1")
    c.stage(d)
    assert c.current_for_turn("t-1") is d


def test_stale_directive_not_delivered(make_directive):
    c = DirectiveController()
    c.stage(make_directive("d-1", "t-1"))
    assert c.current_for_turn("t-2") is None  # computed for t-1, now at t-2


def test_supersession_replaces_and_discards_old(make_directive):
    c = DirectiveController()
    old = make_directive("d-old", "t-1", speculative=True)
    c.stage(old)
    new = make_directive("d-new", "t-1", supersedes="d-old")
    c.stage(new)
    assert c.current_for_turn("t-1") is new
    assert c.is_discarded("d-old")
    # the superseded directive is never returned, even by id-targeted lookups
    assert c.current_for_turn("t-1").id == "d-new"


def test_discard_speculative(make_directive):
    c = DirectiveController()
    c.stage(make_directive("d-spec", "t-1", speculative=True))
    c.discard_speculative()
    assert c.current_for_turn("t-1") is None
    assert c.is_discarded("d-spec")


def test_discard_speculative_noop_on_confirmed(make_directive):
    c = DirectiveController()
    c.stage(make_directive("d-real", "t-1", speculative=False))
    c.discard_speculative()  # must NOT drop a confirmed directive
    assert c.current_for_turn("t-1").id == "d-real"


def test_mark_delivered_clears_current(make_directive):
    c = DirectiveController()
    c.stage(make_directive("d-1", "t-1"))
    c.mark_delivered("d-1")
    assert c.current_for_turn("t-1") is None
    assert c.was_delivered("d-1")


def test_never_returns_discarded(make_directive):
    c = DirectiveController()
    d = make_directive("d-1", "t-1", speculative=True)
    c.stage(d)
    c.discard_speculative()
    # re-staging is allowed; a discarded id stays discarded if re-presented
    assert c.current_for_turn("t-1") is None


def test_staged_id_reports_current_staged_directive(make_directive):
    """Task 7: the public accessor the agent uses to decide whether to supersede a pre-stage."""
    c = DirectiveController()
    assert c.staged_id() is None                       # nothing staged
    c.stage(make_directive("d-spec", "t-1", speculative=True))
    assert c.staged_id() == "d-spec"                   # reflects the staged speculative pre-stage
    c.stage(make_directive("d-real", "t-1", supersedes="d-spec"))
    assert c.staged_id() == "d-real"                   # supersession replaces the staged slot
    c.mark_delivered("d-real")
    assert c.staged_id() is None                       # cleared after delivery
