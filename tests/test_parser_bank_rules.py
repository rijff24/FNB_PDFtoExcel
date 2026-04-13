from app.services.parser import parse_transactions_from_text


def test_parse_capitec_business_text_rule() -> None:
    text = (
        "02/03/26 28/02/26 POS Local Purchase REF123 -467.75 +19 282.44\n"
        "02/03/26 02/03/26 Deposit Transfer From 1054451087 +5 000.00 +14 570.14\n"
    )
    rows = parse_transactions_from_text(text, bank_id="capitec")
    assert len(rows) == 2
    assert rows[0]["amount"] == -467.75
    assert rows[0]["balance"] == 19282.44
    assert rows[1]["amount"] == 5000.0
    assert rows[0]["transaction_date"] == "2026-02-28"


def test_parse_capitec_business_reference_continuation() -> None:
    text = (
        "Balance brought forward +19 750.19\n"
        "02/03/26 28/02/26 POS Local Purchase 0000000000003252 BOSVELD -467.75 +19 282.44\n"
        "BILTONG WAREHO AUTH ID 994046\n"
        "03/03/26 28/02/26 POS Local Purchase 0000000000003252 ACSA JIA JHB -240.00 +19 042.44\n"
    )
    rows = parse_transactions_from_text(text, bank_id="capitec")
    assert len(rows) == 3
    assert rows[0]["description"] == "Balance brought forward"
    assert rows[0]["date"] is None
    assert rows[0]["amount"] is None
    assert rows[0]["balance"] == 19750.19
    assert rows[1]["description"] == "POS Local Purchase"
    assert "0000000000003252 BOSVELD" in rows[1]["reference"]
    assert "AUTH ID 994046" in rows[1]["reference"]


def test_parse_capitec_personal_text_rule() -> None:
    text = (
        "01/03/2026 Spar Gauteng North (Card 7915) -391.44 645.40\n"
        "02/03/2026 Payment Received: Rijff Other Income 5 000.00 5 645.40\n"
    )
    rows = parse_transactions_from_text(text, bank_id="capitec_personal")
    assert len(rows) == 2
    assert rows[0]["amount"] == -391.44
    assert rows[1]["amount"] == 5000.0
    assert rows[1]["balance"] == 5645.40


def test_parse_standard_bank_text_rule() -> None:
    text = (
        "IB PAYMENT TO G LUBBE HILUX KIP 10,120.00- 01 14 2,506,865.77\n"
        "CHEQUE CARD PURCHASE 378.00- 01 15 2,499,040.67\n"
    )
    rows = parse_transactions_from_text(text, bank_id="standard_bank")
    assert len(rows) == 2
    assert rows[0]["amount"] == -10120.0
    assert rows[0]["balance"] == 2506865.77
    assert rows[1]["amount"] == -378.0
