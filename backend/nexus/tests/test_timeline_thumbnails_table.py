from app.main import _TENANT_SCOPED_TABLES
from app.modules.vision.models import SessionTimelineThumbnail


def test_table_is_registered_tenant_scoped():
    assert "session_timeline_thumbnails" in _TENANT_SCOPED_TABLES


def test_model_has_required_columns():
    cols = {c.name for c in SessionTimelineThumbnail.__table__.columns}
    assert {"id", "tenant_id", "session_id", "kind", "ref_id", "t_ms",
            "s3_key", "created_at"} <= cols


def test_unique_constraint_on_session_kind_ref():
    uniques = [
        tuple(c.name for c in con.columns)
        for con in SessionTimelineThumbnail.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    ]
    assert ("session_id", "kind", "ref_id") in uniques
