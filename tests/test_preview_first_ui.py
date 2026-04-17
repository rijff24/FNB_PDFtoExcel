from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services.auth import AuthorizedUser


ROOT = Path(__file__).resolve().parents[1]


def test_home_page_has_preview_only_action() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "Preview &amp; review" in html
    assert "Download Excel" not in html
    assert "Usage this month" in html
    assert "/billing" in html
    assert 'id="firstUseBillingModal"' in html
    assert 'id="firstUseBillingText"' in html
    assert "Before your first Statement Review" in html


def test_help_center_uses_help_docs_only(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.upload.authenticate_request",
        lambda _auth, request=None, path=None: AuthorizedUser(email="user@example.com", uid="u1"),
    )
    client = TestClient(app)
    response = client.get("/help", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    html = response.text
    assert "Getting Started" in html
    assert "Review and Export" in html
    assert "Architecture" not in html
    assert "API Reference" not in html


def test_core_templates_render_with_safe_mocks(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.upload.authenticate_request",
        lambda _auth, request=None, path=None: AuthorizedUser(email="user@example.com", uid="u1"),
    )
    client = TestClient(app)

    checks = [
        ("/", "Bank Statement To Excel"),
        ("/billing", "Billing &amp; Usage"),
        ("/register", "Request Access"),
        ("/admin", "Admin Panel"),
        ("/review?session_id=test-session", "Statement Review"),
        ("/help", "Help Center"),
    ]
    for path, expected in checks:
        response = client.get(path, headers={"Authorization": "Bearer token"})
        assert response.status_code == 200, path
        assert expected in response.text, path


def test_review_page_uses_external_css_and_stable_controls(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routes.upload.authenticate_request",
        lambda _auth, request=None, path=None: AuthorizedUser(email="user@example.com", uid="u1"),
    )
    client = TestClient(app)

    response = client.get("/review?session_id=test-session", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    html = response.text

    assert '<link rel="stylesheet" href="/static/review.css"' in html
    assert "<style>" not in html
    for control_id in [
        "pdfPanel",
        "tablePanel",
        "tableToolbar",
        "transactionsTable",
        "saveBtn",
        "downloadBtn",
        "highlightToggle",
        "scrollModeSegment",
        "tableZoomSlider",
        "syncZoomToggle",
    ]:
        assert f'id="{control_id}"' in html
    assert "toolbar-group" in html
    assert 'id="tableStickyX"' in html


def test_shared_css_defines_swan_design_contract() -> None:
    style_css = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
    review_css = (ROOT / "app" / "static" / "review.css").read_text(encoding="utf-8")
    combined_css = style_css + "\n" + review_css

    for token in [
        "--swan-navy: #002239",
        "--swan-blue: #397fc5",
        "--swan-teal: #36bfb1",
        "--swan-orange: #f58220",
        "--swan-purple: #701878",
    ]:
        assert token in style_css
    assert 'font-family: "Manrope", Arial, sans-serif' in style_css
    assert "linear-gradient(90deg, #701878 -19.48%, #397fc5 29.97%, #36bfb1 101.23%)" in style_css
    assert "--swan-gradient-subtle: linear-gradient(90deg, rgba(112, 24, 120, 0.16), rgba(57, 127, 197, 0.18), rgba(54, 191, 177, 0.20));" in style_css
    assert "background: #001a2c;" in style_css
    assert "border: 1px solid rgba(54, 191, 177, 0.28);" in style_css
    assert "input::file-selector-button" in style_css
    assert "select option" in style_css
    assert "[hidden]" in style_css
    assert ".modal-card h2" in style_css
    assert "var(--swan-gradient-subtle),\n        #001a2c;" in style_css
    assert "var(--swan-gradient-subtle),\n        #001a2c;" in review_css
    assert ".table-toolbar button," not in review_css
    assert ".table-toolbar button:hover" not in review_css
    assert ".table-toolbar button:disabled" not in review_css

    old_palette_markers = ["#175cd3", "#020817", "#0b1120", "#1d4ed8", "#f5f7fb"]
    for marker in old_palette_markers:
        assert marker not in combined_css


def test_review_table_auto_fit_contract() -> None:
    review_html = (ROOT / "app" / "templates" / "review.html").read_text(encoding="utf-8")
    review_css = (ROOT / "app" / "static" / "review.css").read_text(encoding="utf-8")

    assert "let hasAutoFittedColumns = false;" in review_html
    assert "let hasUserResizedColumns = false;" in review_html
    assert "function autoFitTransactionColumnsToPanel()" in review_html
    assert "transactionsTableWrapper.clientWidth" in review_html
    assert "getColumnFitSpec" in review_html
    assert 'if (field === "date") return { min: 108, weight: 1.2 };' in review_html
    assert 'if (field === "post_date") return { min: 112, weight: 1.2 };' in review_html
    assert 'if (field === "transaction_date") return { min: 116, weight: 1.2 };' in review_html
    assert "function getColumnResizeMinimum(index)" in review_html
    assert "refreshTableStickyX();" in review_html
    assert "if (!hasAutoFittedColumns && !hasUserResizedColumns)" in review_html
    assert "hasUserResizedColumns = true;" in review_html
    assert ".col-date { width: 108px; }" in review_css
    assert ".col-post-date { width: 112px; }" in review_css
    assert ".col-transaction-date { width: 116px; }" in review_css


def test_first_use_billing_dialog_contract() -> None:
    auth_js = (ROOT / "app" / "static" / "firebase-auth.js").read_text(encoding="utf-8")
    billing_html = (ROOT / "app" / "templates" / "billing.html").read_text(encoding="utf-8")
    admin_html = (ROOT / "app" / "templates" / "admin.html").read_text(encoding="utf-8")

    assert "statementReviewFirstUseBillingAck" in auth_js
    assert "function isFirstBillingPoolUse(data)" in auth_js
    assert "function showFirstUseBillingDialog(data, user)" in auth_js
    assert "monthly_platform_cost_zar" in auth_js
    assert "Checking billing..." in auth_js
    assert "Billing pool: \" + poolLabel" in billing_html
    assert "firstUsePlatformCost" in billing_html
    assert "The first successful Statement Review in a billing pool" in billing_html
    assert "function organizationNameForId(orgId)" in admin_html
    assert "organizationLabelForId(user.org_id)" in admin_html


def test_review_workspace_equal_height_contract() -> None:
    review_css = (ROOT / "app" / "static" / "review.css").read_text(encoding="utf-8")
    review_html = (ROOT / "app" / "templates" / "review.html").read_text(encoding="utf-8")

    assert "body.review-page" in review_css
    assert "height: 100vh;" in review_css
    assert "overflow: hidden;" in review_css
    assert ".review-container" in review_css
    assert "display: flex;" in review_css
    assert "flex-direction: column;" in review_css
    assert "min-height: 0;" in review_css
    assert ".review-layout" in review_css
    assert "flex: 1 1 0;" in review_css
    assert "min-height: 0;" in review_css
    assert ".pdf-viewer" in review_css
    assert "height: auto;" in review_css
    assert "padding: 0 10px;" in review_css
    assert "scrollbar-gutter: stable;" in review_css
    assert ".pdf-canvas-wrapper" in review_css
    assert "flex: 1 0 auto;" in review_css
    assert ".transactions-card" in review_css
    assert ".transactions-table-wrapper" in review_css
    assert "max-height: none;" in review_css
    assert ".sticky-x-scroll" in review_css
    assert "bottom: 0;" in review_css
    assert 'id="zoomValue">100%</span>' in review_html
    assert "let currentZoom = 1.0;" in review_html
    assert "function getPdfAvailableWidth()" in review_html
    assert "function getPdfRenderScale(page)" in review_html
    assert "var baseViewport = page.getViewport({ scale: 1 });" in review_html
    assert "var viewport = page.getViewport({ scale: getPdfRenderScale(page) });" in review_html
