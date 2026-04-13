from app.services import cost_reconciliation


def test_run_reconciliation_warning_when_variance_exceeds_threshold(monkeypatch) -> None:
    today_key = cost_reconciliation.datetime.now(cost_reconciliation._recon_tz()).date().isoformat()
    monkeypatch.setenv("RECONCILIATION_ALERT_PCT", "10")
    monkeypatch.setattr(
        "app.services.cost_reconciliation._fetch_app_usage_by_day",
        lambda *_args, **_kwargs: {today_key: {"app_estimate": 10.0, "ocr_docs": 2.0, "ocr_pages": 20.0}},
    )
    monkeypatch.setattr(
        "app.services.cost_reconciliation._fetch_financial_truth_by_day",
        lambda *_args, **_kwargs: ({today_key: 5.0}, True),
    )
    monkeypatch.setattr("app.services.cost_reconciliation._persist_rows", lambda _rows: None)

    result = cost_reconciliation.run_reconciliation(days=1, persist=True)
    assert result["summary"]["status"] == "warning"
    assert result["rows"][0]["status"] == "warning"


def test_run_reconciliation_awaiting_data_when_truth_missing(monkeypatch) -> None:
    today_key = cost_reconciliation.datetime.now(cost_reconciliation._recon_tz()).date().isoformat()
    monkeypatch.setenv("RECONCILIATION_ALERT_PCT", "15")
    monkeypatch.setattr(
        "app.services.cost_reconciliation._fetch_app_usage_by_day",
        lambda *_args, **_kwargs: {today_key: {"app_estimate": 3.0, "ocr_docs": 1.0, "ocr_pages": 8.0}},
    )
    monkeypatch.setattr(
        "app.services.cost_reconciliation._fetch_financial_truth_by_day",
        lambda *_args, **_kwargs: ({}, False),
    )
    monkeypatch.setattr("app.services.cost_reconciliation._persist_rows", lambda _rows: None)

    result = cost_reconciliation.run_reconciliation(days=1, persist=True)
    assert result["summary"]["status"] == "awaiting_data"
    assert result["rows"][0]["status"] == "awaiting_data"
