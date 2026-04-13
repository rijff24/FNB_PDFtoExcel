from app.services.billing import (
    BillingSettings,
    DEFAULT_TIER_BRACKETS,
    DocumentCostBreakdown,
    calculate_document_cost,
    evaluate_limits,
    generate_tier_table,
    tier_price_usd,
)


def test_tier_price_bracket_lookup() -> None:
    assert tier_price_usd(n=1) == 3.12
    assert tier_price_usd(n=5) == 3.12
    assert tier_price_usd(n=6) == 2.35
    assert tier_price_usd(n=10) == 2.35
    assert tier_price_usd(n=11) == 1.65
    assert tier_price_usd(n=50) == 1.10
    assert tier_price_usd(n=100) == 0.98
    assert tier_price_usd(n=500) == 0.89
    assert tier_price_usd(n=1000) == 0.82
    assert tier_price_usd(n=1001) == 0.81
    assert tier_price_usd(n=99999) == 0.81


def test_tier_price_decreases_with_volume() -> None:
    p1 = tier_price_usd(n=1)
    p50 = tier_price_usd(n=50)
    p1000 = tier_price_usd(n=1000)
    p1001 = tier_price_usd(n=1001)
    assert p1 > p50 > p1000 > p1001


def test_tier_price_custom_brackets() -> None:
    custom = [
        {"min_docs": 1, "max_docs": 10, "price_usd": 5.0},
        {"min_docs": 11, "max_docs": None, "price_usd": 3.0},
    ]
    assert tier_price_usd(n=1, brackets=custom) == 5.0
    assert tier_price_usd(n=10, brackets=custom) == 5.0
    assert tier_price_usd(n=11, brackets=custom) == 3.0
    assert tier_price_usd(n=9999, brackets=custom) == 3.0


def test_calculate_document_cost_bracket() -> None:
    cost = calculate_document_cost(
        document_count=1,
        current_volume=10,
        google_usd_per_document=0.75,
        margin_per_document_usd=0.05,
        usd_to_zar=18.5,
        infra_monthly_usd=9.30,
    )
    assert isinstance(cost, DocumentCostBreakdown)
    assert cost.documents == 1
    assert cost.tier_price_usd == 2.35
    assert cost.billable_total_zar == round(2.35 * 18.5, 6)
    assert cost.google_cost_total_zar == round(0.75 * 18.5, 6)
    assert cost.our_margin_amount_zar == round(0.05 * 18.5, 6)


def test_generate_tier_table_has_labels() -> None:
    table = generate_tier_table(usd_to_zar=18.5)
    assert len(table) == len(DEFAULT_TIER_BRACKETS)
    assert table[0]["label"] == "1\u20135"
    assert table[-1]["label"] == "1001+"
    assert all("price_usd" in r and "price_zar" in r and "label" in r for r in table)
    assert table[0]["price_usd"] > table[-1]["price_usd"]


def test_evaluate_limits_warn_then_block() -> None:
    settings = BillingSettings(monthly_limit_amount=100.0, warn_pct=80.0, hard_stop_enabled=True)
    warn = evaluate_limits(settings=settings, current_total=70.0, projected_total=85.0)
    assert warn.blocked is False
    assert warn.warning is not None

    block = evaluate_limits(settings=settings, current_total=95.0, projected_total=101.0)
    assert block.blocked is True
    assert block.limit_remaining == 0.0
