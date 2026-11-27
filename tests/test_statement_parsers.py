from datetime import date

import pytest

from app.models.transaction import Direction
from app.services.parsers.statement_base import StatementDocument, StatementParser
from app.services.parsers.statements import BANK_PARSERS, parse_document
from app.services.parsers.statements.layout import detect_kind
from app.services.parsers.statements.money import parse_money
from app.services.statements.documents import (
    DocumentError,
    PasswordInvalid,
    PasswordRequired,
    decrypt_pdf,
    extract_csv,
    extract_pdf,
    sniff_kind,
)
from app.services.statements.reconcile import compute_reconciliation
from tests.statement_fixtures import (
    STYLES,
    build_account,
    build_card,
    render_pdf,
    render_table_pdf,
)

CARD_BANKS = [k for k, s in STYLES.items() if s.has_card]


def _expected(built):
    return [
        (built.posted[i], t.amount, Direction.credit if t.credit else Direction.debit)
        for i, t in enumerate(built.txns)
    ]


def _doc(built, password=None):
    data = render_pdf(built.lines, password)
    return extract_pdf(decrypt_pdf(data, [password] if password else []))


@pytest.mark.parametrize("key", CARD_BANKS)
def test_card_statement_per_bank(key):
    built = build_card(STYLES[key])
    out = parse_document(_doc(built))
    st = out.statement
    assert st is not None and out.institution == key and st.institution == key
    assert st.kind == "credit_card" and st.parser == f"{key}:credit_card"
    assert [(ln.posted_on, ln.amount_minor, ln.direction) for ln in st.lines] == _expected(built)
    assert (st.opening_minor, st.closing_minor, st.min_due_minor) == (
        built.opening,
        built.closing,
        built.min_due,
    )
    assert st.last4 == "1234" and st.currency == "AED"
    assert st.period_start == date(2026, 2, 25) and st.period_end == date(2026, 3, 25)
    assert st.credit_limit_minor == 2_000_000  # the printed limit, for filling a card added by name only
    assert st.due_date == date(2026, 4, 19) and st.statement_date == date(2026, 3, 25)
    assert compute_reconciliation(st.kind, st.opening_minor, st.closing_minor, st.lines).status == "matched"


def test_card_foreign_amount_and_wrapped_description():
    st = parse_document(_doc(build_card(STYLES["enbd"]))).statement
    assert st is not None
    amazon = next(ln for ln in st.lines if "AMAZON" in ln.description)
    assert (amazon.amount_minor, amazon.foreign_amount_minor, amazon.foreign_currency) == (3673, 1000, "USD")
    assert amazon.description == "AMAZON US SEATTLE US"
    uber = next(ln for ln in st.lines if "UBER" in ln.description)
    assert uber.description == "UBER *TRIP HELP.UBER.COM AMSTERDAM NL"
    # foreign amount after the AED amount (RAKBANK layout)
    rak = parse_document(_doc(build_card(STYLES["rakbank"]))).statement
    assert rak is not None
    a = next(ln for ln in rak.lines if "AMAZON" in ln.description)
    assert (a.amount_minor, a.foreign_amount_minor, a.foreign_currency) == (3673, 1000, "USD")


def test_card_summary_totals():
    built = build_card(STYLES["enbd"])
    st = parse_document(_doc(built)).statement
    assert st is not None
    debits = sum(t.amount for t in built.txns if not t.credit)
    credits_ = sum(t.amount for t in built.txns if t.credit)
    assert (st.total_purchases_minor, st.total_payments_minor) == (debits, credits_)


@pytest.mark.parametrize("key", list(STYLES))
def test_account_statement_per_bank(key):
    built = build_account(STYLES[key])
    out = parse_document(_doc(built))
    st = out.statement
    assert st is not None and st.institution == key and st.kind == "bank_account"
    assert [(ln.posted_on, ln.amount_minor, ln.direction) for ln in st.lines] == _expected(built)
    assert (st.opening_minor, st.closing_minor) == (built.opening, built.closing)
    assert st.last4 == "0123"
    assert st.lines[-1].balance_minor == built.closing
    assert compute_reconciliation(st.kind, st.opening_minor, st.closing_minor, st.lines).status == "matched"


def test_account_direction_comes_from_running_balance_not_markers():
    # Same amounts, opposite directions: only the balance column can tell them apart.
    from tests.statement_fixtures import Txn

    txns = [Txn(1, "TRANSFER A", 10000, credit=True), Txn(2, "TRANSFER B", 10000)]
    st = parse_document(_doc(build_account(STYLES["enbd"], txns, opening=0))).statement
    assert st is not None
    assert [ln.direction for ln in st.lines] == [Direction.credit, Direction.debit]


def test_mismatch_is_reported_not_hidden():
    built = build_card(STYLES["enbd"])
    lines = [ln.replace("120.50", "121.50") if ln.startswith("02 Mar") else ln for ln in built.lines]
    st = parse_document(extract_pdf(render_pdf(lines))).statement
    assert st is not None
    rec = compute_reconciliation(st.kind, st.opening_minor, st.closing_minor, st.lines)
    assert (
        rec.status == "mismatch" and rec.diff_minor == -100
    )  # printed closing is 1.00 lower than the lines sum


def test_unknown_bank_uses_generic_layout_parser():
    built = build_card(STYLES["enbd"])
    lines = [ln.replace("EMIRATES NBD", "SOME OTHER BANK") for ln in built.lines]
    out = parse_document(extract_pdf(render_pdf(lines)))
    assert out.institution is None and out.statement is not None
    assert out.statement.parser.startswith("generic-layout") and len(out.statement.lines) == len(built.txns)
    assert out.statement.institution is None


def test_explicit_institution_hint_beats_detection():
    built = build_card(STYLES["enbd"])
    out = parse_document(_doc(built), institution="Emirates NBD")
    assert out.institution == "enbd"


def test_every_institution_has_a_statement_parser():
    from app.services.institutions import institutions

    assert set(BANK_PARSERS) == set(institutions.keys)
    assert all(isinstance(p, StatementParser) for p in BANK_PARSERS.values())


def test_table_heuristic_on_ruled_pdf_table():
    header = ["Date", "Description", "Debit", "Credit", "Balance"]
    rows = [
        ["01/03/2026", "POS CARREFOUR\nDUBAI", "120.50", "", "4,879.50"],
        ["03/03/2026", "SALARY ACME", "", "15,000.00", "19,879.50"],
        ["06/03/2026", "ATM WITHDRAWAL", "500.00", "", "19,379.50"],
    ]
    data = render_table_pdf(
        ["Some Unknown Bank", "Account Statement", "Opening Balance 5,000.00", "Closing Balance 19,379.50"],
        header,
        rows,
    )
    out = parse_document(extract_pdf(data))
    st = out.statement
    assert st is not None and st.parser.startswith(("tables", "generic-layout"))
    assert [(ln.amount_minor, ln.direction) for ln in st.lines] == [
        (12050, Direction.debit),
        (1500000, Direction.credit),
        (50000, Direction.debit),
    ]
    assert st.lines[0].description == "POS CARREFOUR DUBAI"
    assert (st.opening_minor, st.closing_minor) == (500000, 1937950)


def test_csv_bank_account_with_signed_amounts_and_balance():
    csv_bytes = (
        b"Date,Description,Amount,Balance\n"
        b"01/03/2026,POS CARREFOUR,-120.50,4879.50\n"
        b"03/03/2026,SALARY,15000.00,19879.50\n"
    )
    assert sniff_kind(csv_bytes) == "csv"
    st = parse_document(extract_csv(csv_bytes)).statement
    assert st is not None and st.kind == "bank_account"
    assert [(ln.amount_minor, ln.direction) for ln in st.lines] == [
        (12050, Direction.debit),
        (1500000, Direction.credit),
    ]
    # no summary block: opening/closing derived from the balance column
    assert (st.opening_minor, st.closing_minor) == (500000, 1987950)
    assert compute_reconciliation(st.kind, st.opening_minor, st.closing_minor, st.lines).status == "matched"


def test_csv_semicolon_card_export_positive_is_purchase():
    csv_bytes = (
        "Transaction Date;Details;Amount (AED);Foreign Amount;Foreign Currency\n"
        "02-03-2026;CARREFOUR;120,50;;\n".replace("120,50", "120.50")
        + "05-03-2026;PAYMENT RECEIVED;-500.00;;\n"
        + "07-03-2026;AMAZON;36.73;10.00;USD\n"
    ).encode()
    doc = extract_csv(csv_bytes)
    st = parse_document(doc, kind="credit_card").statement
    assert st is not None
    assert [(ln.amount_minor, ln.direction) for ln in st.lines] == [
        (12050, Direction.debit),
        (50000, Direction.credit),
        (3673, Direction.debit),
    ]
    assert (st.lines[2].foreign_amount_minor, st.lines[2].foreign_currency) == (1000, "USD")


def test_garbage_yields_no_statement():
    out = parse_document(StatementDocument(text="Hello world\nnothing to see"))
    assert out.statement is None
    assert parse_document(extract_csv(b"a,b\n1,2\n")).statement is None


def test_detect_kind_and_money_tokens():
    assert detect_kind("Credit Card Statement ... Minimum Payment Due") == "credit_card"
    assert detect_kind("Account Statement IBAN AE07") == "bank_account"
    assert parse_money("1,234.50").minor == 123450  # type: ignore[union-attr]
    m = parse_money("(12.00)")
    assert m is not None and m.negative and m.minor == 1200
    m = parse_money("250.00 Cr")
    assert m is not None and m.marker == "CR"
    assert parse_money("AED 10.00").minor == 1000  # type: ignore[union-attr]
    assert parse_money("abc") is None and parse_money("12") is None


def test_encrypted_pdf_roundtrip_and_errors():
    built = build_card(STYLES["adcb"])
    data = render_pdf(built.lines, password="s3cret")
    assert sniff_kind(data) == "pdf"
    with pytest.raises(PasswordRequired):
        decrypt_pdf(data, [])
    with pytest.raises(PasswordInvalid):
        decrypt_pdf(data, ["wrong", "also-wrong"])
    plain = decrypt_pdf(data, ["wrong", "s3cret"])
    st = parse_document(extract_pdf(plain)).statement
    assert st is not None and st.institution == "adcb" and len(st.lines) == len(built.txns)
    # unencrypted input is returned untouched, passwords are ignored
    clean = render_pdf(built.lines)
    assert decrypt_pdf(clean, ["whatever"]) == clean


def test_corrupt_pdf_raises_document_error():
    with pytest.raises(DocumentError):
        decrypt_pdf(b"%PDF-1.4 not really a pdf")
    assert sniff_kind(b"\x00\x01\x02binary") == "unknown"


def test_multi_page_statement():
    from tests.statement_fixtures import Txn

    txns = [Txn(1 + (i % 28), f"SHOP NUMBER {i} DUBAI", 1000 + i) for i in range(120)]
    built = build_card(STYLES["enbd"], txns)
    doc = extract_pdf(render_pdf(built.lines, per_page=45))
    assert doc.pages >= 3
    st = parse_document(doc).statement
    assert st is not None and len(st.lines) == 120
    assert compute_reconciliation(st.kind, st.opening_minor, st.closing_minor, st.lines).status == "matched"
