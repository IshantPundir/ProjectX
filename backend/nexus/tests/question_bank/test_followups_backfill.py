from app.migrations_support import followups_backfill as bf


def test_slug_basic():
    assert bf.slug("How would you validate impact before adjusting a policy?") \
        == "how_would_you_validate_impact_before_adjusting_a_policy"


def test_slug_truncates_and_collapses():
    s = bf.slug("A/B  test!!  rollout — safely")
    assert s == "a_b_test_rollout_safely"
    assert len(s) <= 60


def test_upgrade_wraps_strings():
    out = bf.upgrade_value(["Probe one?", "Probe two?"])
    assert out == [
        {"dimension": "probe_one", "intent": "Probe one?", "seed_probe": "Probe one?", "listen_for": []},
        {"dimension": "probe_two", "intent": "Probe two?", "seed_probe": "Probe two?", "listen_for": []},
    ]


def test_upgrade_dedups_duplicate_slugs():
    out = bf.upgrade_value(["Same probe", "Same probe"])
    assert out[0]["dimension"] == "same_probe"
    assert out[1]["dimension"] == "same_probe_2"


def test_upgrade_is_idempotent_on_objects():
    already = [{"dimension": "d", "intent": "i", "seed_probe": "p", "listen_for": []}]
    assert bf.upgrade_value(already) == already


def test_downgrade_takes_seed_probe():
    objs = [{"dimension": "d", "intent": "i", "seed_probe": "P1", "listen_for": ["x"]}]
    assert bf.downgrade_value(objs) == ["P1"]


def test_downgrade_is_idempotent_on_strings():
    assert bf.downgrade_value(["P1", "P2"]) == ["P1", "P2"]
