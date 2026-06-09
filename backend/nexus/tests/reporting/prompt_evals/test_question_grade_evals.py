import pytest
from app.modules.interview_runtime.evidence import (
    EvidenceNote, EvidenceStance, EvidenceTexture, TimeSpan,
)
from app.modules.reporting.scoring.question_grade import grade_question

pytestmark = pytest.mark.prompt_quality


def _n(seq, texture, quote):
    return EvidenceNote(seq=seq, turn_ref=f"t{seq}", signal="s",
                        stance=EvidenceStance.supports, texture=texture, quote=quote,
                        span=TimeSpan(start_ms=0, end_ms=1), from_question_id="q1",
                        via_probe=False, retracts_seq=None)


@pytest.mark.asyncio
async def test_thin_dedicated_answer_grades_thin_and_trips_red_flag():
    q = {"id": "q1", "text": "An iOS update breaks Wi-Fi on managed iPhones. Diagnose with Intune.",
         "rubric": {"excellent": "Names APNs/cert profile checks, isolates by supervision, targeted rollback.",
                    "meets_bar": "Basic profile checks and a reasonable rollback plan.",
                    "below_bar": "Vague blame, suggests wipe, no Intune/iOS specifics."},
         "positive_evidence": ["Checks Wi-Fi config and certificate profiles (SCEP/PKCS)"],
         "red_flags": ["Does not know Wi-Fi or certificate profile handling on iOS/Intune"],
         "evaluation_hint": "Listen for cert/profile specifics.",
         "question_kind": "technical_scenario", "difficulty": "medium"}
    notes = [_n(1, EvidenceTexture.thin, "We can check the Wi Fi policy."),
             _n(2, EvidenceTexture.thin, "And look into it.")]
    out = await grade_question(question=q, notes=notes, probes_used=3, probes_available=3,
                               base_level="thin", correlation_id="cid-eval")
    assert out.level in ("thin", "absent")
    assert any("Wi" in r or "cert" in r.lower() for r in out.red_flags_tripped)


@pytest.mark.asyncio
async def test_probe_dependence_caps_one_tier():
    q = {"id": "q1", "text": "Tell me about an Intune change you executed.",
         "rubric": {"excellent": "Names the exact object changed, personal build/test/target/validate steps, change control, documentation, rollback/outcome.",
                    "meets_bar": "A concrete Intune change with some personal involvement and basic documentation.",
                    "below_bar": "Team-only narrative, no Intune specifics, no documentation."},
         "positive_evidence": ["Names a specific Intune artifact changed",
                               "Describes personal build/test/target/validate steps",
                               "Documentation produced (KB/runbook/change record)"],
         "red_flags": ["Speaks only in 'we' with no personal actions"],
         "evaluation_hint": "Listen for a crisp STAR story with personal ownership.",
         "question_kind": "behavioral", "difficulty": "medium"}
    notes = [
        EvidenceNote(seq=1, turn_ref="t1", signal="s", stance=EvidenceStance.supports,
                     texture=EvidenceTexture.strong,
                     quote="I changed the iOS compliance policy, tested the rollback first, "
                           "documented it in BMC Remedy with validation screenshots.",
                     span=TimeSpan(start_ms=0, end_ms=1), from_question_id="q1",
                     via_probe=True, retracts_seq=None),
    ]
    out = await grade_question(question=q, notes=notes, probes_used=3, probes_available=3,
                               base_level="strong", correlation_id="cid-probe")
    assert out.level == "solid"
