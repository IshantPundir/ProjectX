from app.modules.interview_engine.models.judge import ClaimEntry as JudgeClaimEntry
from app.modules.interview_engine.state.claims import CandidateClaimsPool


def _judge_claim(topic: str) -> JudgeClaimEntry:
    return JudgeClaimEntry(
        claim_topic=topic,
        claim_text=f"text for {topic}",
        source_quote=f"quote for {topic}",
    )


def test_empty_initial_pool():
    pool = CandidateClaimsPool(max_size=50)
    assert pool.snapshot().entries == []


def test_add_canonicalizes_with_capture_metadata():
    pool = CandidateClaimsPool(max_size=50)
    pool.add(_judge_claim("automation"), captured_at_turn=3, captured_at_seq=7)
    snap = pool.snapshot()
    assert len(snap.entries) == 1
    e = snap.entries[0]
    assert e.claim_topic == "automation"
    assert e.captured_at_turn == 3
    assert e.captured_at_seq == 7


def test_drop_oldest_at_cap():
    pool = CandidateClaimsPool(max_size=3)
    for i in range(4):
        pool.add(_judge_claim(f"topic-{i}"), captured_at_turn=i, captured_at_seq=i + 1)
    snap = pool.snapshot()
    assert len(snap.entries) == 3
    assert [e.claim_topic for e in snap.entries] == ["topic-1", "topic-2", "topic-3"]


def test_from_snapshot_round_trip():
    pool = CandidateClaimsPool(max_size=50)
    pool.add(_judge_claim("a"), captured_at_turn=1, captured_at_seq=1)
    pool.add(_judge_claim("b"), captured_at_turn=2, captured_at_seq=2)
    snap = pool.snapshot()
    pool2 = CandidateClaimsPool.from_snapshot(snap, max_size=50)
    assert pool2.snapshot().entries == snap.entries
