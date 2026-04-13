from fastapi.testclient import TestClient

from app.main import app


def test_home_page_has_preview_only_action() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "Preview &amp; review" in html
    assert "Download Excel" not in html
    assert "Usage this month" in html
    assert "/billing" in html
