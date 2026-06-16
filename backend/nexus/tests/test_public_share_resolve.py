import inspect


def test_public_share_exports():
    from app.modules.reporting.public_share import (
        build_public_envelope,
        resolve_share_token,
    )
    assert inspect.iscoroutinefunction(resolve_share_token)
    assert inspect.iscoroutinefunction(build_public_envelope)


def test_envelope_schema_fields():
    from app.modules.reporting.schemas import PublicRecordingsEnvelope
    fields = set(PublicRecordingsEnvelope.model_fields.keys())
    for f in ("candidate_name", "job_title", "stage_label",
              "report", "recording", "proctoring", "reel"):
        assert f in fields
