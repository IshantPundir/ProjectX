from contextlib import asynccontextmanager

import pytest

from app.modules.reporting import actors


def _patch_bypass_session(monkeypatch, db):
    """Make the actor reuse the per-test rollback transaction."""
    @asynccontextmanager
    async def _fake_bypass_session():
        yield db

    monkeypatch.setattr(actors, "get_bypass_session", _fake_bypass_session)


@pytest.mark.asyncio
async def test_share_actor_renders_uploads_emails(db_session, monkeypatch, seeded_share):
    """Happy path: pending row -> rendered -> uploaded -> emailed -> sent."""
    sent = {}

    async def fake_render(ctx):
        sent["ctx"] = ctx
        return b"%PDF-1.4 fake"

    class FakeStorage:
        async def upload_bytes(self, key, data, *, content_type):
            sent["key"] = key
            sent["bytes"] = data
        async def presign_get_url(self, key, *, ttl_seconds):
            return "https://r2/photo.jpg"

    async def fake_send_email(*, to, subject, html, attachments=None):
        sent["to"] = to
        sent["attachments"] = attachments

    _patch_bypass_session(monkeypatch, db_session)
    monkeypatch.setattr(actors, "render_report_pdf", fake_render)
    monkeypatch.setattr(actors, "get_object_storage", lambda: FakeStorage())
    monkeypatch.setattr(actors, "send_email", fake_send_email)

    await actors._share_report_pdf_async(
        share_id=seeded_share.id, tenant_id=seeded_share.tenant_id, correlation_id="corr-1",
    )

    await db_session.refresh(seeded_share)
    assert seeded_share.status == "sent"
    assert seeded_share.pdf_r2_key == sent["key"]
    assert sent["to"] == "client@acme.com"
    assert sent["attachments"][0][2] == "application/pdf"
    assert sent["attachments"][0][1] == b"%PDF-1.4 fake"


@pytest.mark.asyncio
async def test_share_actor_idempotent_when_sent(db_session, monkeypatch, seeded_share):
    seeded_share.status = "sent"
    await db_session.commit()

    called = {"render": False}

    async def fake_render(ctx):
        called["render"] = True
        return b""

    _patch_bypass_session(monkeypatch, db_session)
    monkeypatch.setattr(actors, "render_report_pdf", fake_render)
    await actors._share_report_pdf_async(
        share_id=seeded_share.id, tenant_id=seeded_share.tenant_id, correlation_id="c",
    )
    assert called["render"] is False  # short-circuited


@pytest.mark.asyncio
async def test_share_actor_mints_token_and_sets_recordings_url(
    db_session, monkeypatch, seeded_share
):
    """The actor mints an opaque share token, persists only its HMAC hash +
    expiry on the row, and points the PDF button at /recordings/<token>."""
    from app.modules.reporting.share_tokens import hash_share_token

    captured = {}

    real_build = actors.build_pdf_context

    def _capture_build(*args, **kwargs):
        captured["full_session_url"] = kwargs.get("full_session_url")
        return real_build(*args, **kwargs)

    async def fake_render(ctx):
        return b"%PDF-1.4 fake"

    class FakeStorage:
        async def upload_bytes(self, key, data, *, content_type):
            pass
        async def presign_get_url(self, key, *, ttl_seconds):
            return "https://r2/photo.jpg"

    async def fake_send_email(*, to, subject, html, attachments=None):
        pass

    _patch_bypass_session(monkeypatch, db_session)
    monkeypatch.setattr(actors, "build_pdf_context", _capture_build)
    monkeypatch.setattr(actors, "render_report_pdf", fake_render)
    monkeypatch.setattr(actors, "get_object_storage", lambda: FakeStorage())
    monkeypatch.setattr(actors, "send_email", fake_send_email)

    await actors._share_report_pdf_async(
        share_id=seeded_share.id, tenant_id=seeded_share.tenant_id, correlation_id="c",
    )

    url = captured["full_session_url"]
    assert "/recordings/" in url
    assert "/shared-session/coming-soon" not in url
    assert url.endswith("?view=full")   # session button deep-links to the full view
    token = url.rsplit("/recordings/", 1)[1].split("?", 1)[0]
    assert token  # non-empty

    await db_session.refresh(seeded_share)
    assert seeded_share.share_token_hash is not None
    assert seeded_share.share_expires_at is not None
    # The stored hash matches the plaintext in the URL; plaintext is NOT stored.
    assert seeded_share.share_token_hash == hash_share_token(token)


@pytest.mark.asyncio
async def test_share_actor_marks_failed_on_render_error(db_session, monkeypatch, seeded_share):
    async def boom(ctx):
        raise RuntimeError("chromium died")

    _patch_bypass_session(monkeypatch, db_session)
    monkeypatch.setattr(actors, "render_report_pdf", boom)
    with pytest.raises(RuntimeError):
        await actors._share_report_pdf_async(
            share_id=seeded_share.id, tenant_id=seeded_share.tenant_id, correlation_id="c",
        )
    await db_session.refresh(seeded_share)
    assert seeded_share.status == "failed"
    assert "chromium died" in (seeded_share.error or "")


@pytest.mark.asyncio
async def test_share_actor_uses_candidate_session_base_url(
    db_session, monkeypatch, seeded_share
):
    """The recordings link is based on candidate_session_base_url (the session
    app), so a shared PDF opened over the session ngrok tunnel reaches a real
    page. Changed 2026-06-29: moved from the recruiter-app base to the session-app base."""
    captured = {}
    real_build = actors.build_pdf_context

    def _capture_build(*args, **kwargs):
        captured["full_session_url"] = kwargs.get("full_session_url")
        captured["reel_url"] = kwargs.get("reel_url")
        return real_build(*args, **kwargs)

    async def fake_render(ctx):
        return b"%PDF-1.4 fake"

    class FakeStorage:
        async def upload_bytes(self, key, data, *, content_type):
            pass
        async def presign_get_url(self, key, *, ttl_seconds):
            return "https://r2/photo.jpg"

    async def fake_send_email(*, to, subject, html, attachments=None):
        pass

    monkeypatch.setattr(actors.settings, "candidate_session_base_url", "https://sess.example.ngrok.app")
    _patch_bypass_session(monkeypatch, db_session)
    monkeypatch.setattr(actors, "build_pdf_context", _capture_build)
    monkeypatch.setattr(actors, "render_report_pdf", fake_render)
    monkeypatch.setattr(actors, "get_object_storage", lambda: FakeStorage())
    monkeypatch.setattr(actors, "send_email", fake_send_email)

    await actors._share_report_pdf_async(
        share_id=seeded_share.id, tenant_id=seeded_share.tenant_id, correlation_id="c",
    )

    full_url = captured["full_session_url"]
    reel_url = captured["reel_url"]
    assert "/recordings/" in full_url
    token = full_url.rsplit("/recordings/", 1)[1].split("?", 1)[0]
    assert token  # non-empty
    assert full_url == "https://sess.example.ngrok.app/recordings/" + token + "?view=full"
    assert reel_url == "https://sess.example.ngrok.app/recordings/" + token + "?view=reel"
