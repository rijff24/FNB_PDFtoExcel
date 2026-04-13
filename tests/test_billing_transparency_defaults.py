"""Defaults for billing transparency include calculator assumptions, scope copy, and tier brackets."""

from app.services.admin_store import _default_billing_pricing_global


def test_default_billing_pricing_has_estimate_scope_and_limits() -> None:
    d = _default_billing_pricing_global()
    assert "transparency_estimate_scope_note" in d
    assert "$9.30" in d["transparency_estimate_scope_note"]
    items = d["pricing_transparency_line_items"]
    assert isinstance(items, list) and len(items) >= 2
    assert any((it.get("estimate_limits") or []) for it in items)


def test_default_billing_pricing_has_tier_brackets() -> None:
    d = _default_billing_pricing_global()
    brackets = d.get("tier_brackets")
    assert isinstance(brackets, list) and len(brackets) >= 5
    assert brackets[0]["min_docs"] == 1
    assert brackets[-1]["max_docs"] is None
    assert brackets[0]["price_usd"] > brackets[-1]["price_usd"]
    assert "google_usd_per_classified_document" in d
    assert "margin_per_document_usd" in d
    assert "infra_monthly_usd" in d
