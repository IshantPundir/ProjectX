from app.modules.notifications.service import render_template


def test_report_share_template_renders():
    html = render_template(
        "report_share.html",
        candidate_name="Ishant Pundir",
        job_title="Jr. Forward Deployed Engineer",
        shared_by="Acme Staffing",
    )
    assert "Ishant Pundir" in html
    assert "Jr. Forward Deployed Engineer" in html
    assert "attached" in html.lower()
