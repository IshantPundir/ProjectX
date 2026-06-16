def test_assets_helpers_importable():
    from app.modules.reporting.assets import (
        attach_question_thumbnails,
        attach_reference_photo,
    )
    assert callable(attach_question_thumbnails)
    assert callable(attach_reference_photo)
