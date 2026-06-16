import pytest

from app.modules.notifications import service as svc


class _CaptureProvider:
    def __init__(self):
        self.calls = []

    async def send(self, *, to, subject, html, attachments=None):
        self.calls.append({"to": to, "subject": subject, "attachments": attachments})


@pytest.mark.asyncio
async def test_send_email_forwards_attachments(monkeypatch):
    cap = _CaptureProvider()
    monkeypatch.setattr(svc, "_provider", cap)
    await svc.send_email(
        to="x@y.com", subject="s", html="<p>h</p>",
        attachments=[("report.pdf", b"%PDF-1.4 ...", "application/pdf")],
    )
    assert cap.calls[0]["attachments"] == [("report.pdf", b"%PDF-1.4 ...", "application/pdf")]


@pytest.mark.asyncio
async def test_send_email_attachments_default_none(monkeypatch):
    cap = _CaptureProvider()
    monkeypatch.setattr(svc, "_provider", cap)
    await svc.send_email(to="x@y.com", subject="s", html="<p>h</p>")
    assert cap.calls[0]["attachments"] is None
