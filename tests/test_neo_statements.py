"""Real-layout quirks of the consolidated multi-account e-statement (synthetic fixtures, see neo_fixture)."""

import re
from datetime import date

import pytest

from app.models.transaction import Direction
from app.services.parsers.statements import parse_document
from app.services.parsers.statements.mashreq import parse_account_sections
from app.services.parsers.statements.money import parse_money3
from app.services.statements.documents import extract_pdf
from app.services.statements.reconcile import compute_reconciliation
from tests.neo_fixture import scenario_a, scenario_b


def _expected_desc(entry):
    text = re.sub(r"VALUE DATE: \d\d-\d\d-\d{4} ?", "", " ".join(entry.lines))
    return re.sub(r"\s+", " ", text).strip()


def _parse(neo):
    doc = extract_pdf(neo.pdf)
    return doc, parse_document(doc)


@pytest.mark.parametrize("scenario", [scenario_a, scenario_b], ids=["a", "b"])
def test_sections_round_trip_with_reconciliation(scenario):
    neo = scenario()
    doc, out = _parse(neo)
    assert out.institution == "mashreq" and out.kind == "bank_account"
    assert out.attempts[0].startswith("mashreq:bank_account")
    assert len(out.sections) == len(neo.accounts) == 2
    assert out.statement is not None and out.statement in out.sections
    for sec, acct in zip(out.sections, neo.accounts, strict=True):
        assert sec.last4 == acct.iban[-4:] and sec.currency == "AED"
        assert (sec.opening_minor, sec.closing_minor) == (acct.opening, acct.closing)
        assert (sec.period_start, sec.period_end) == (neo.start, neo.end)
        assert sec.statement_date == neo.issue and sec.holder_name == "TESTA EXAMPLE-HOLDER"
        assert sec.warnings == []
        assert len(sec.lines) == len(acct.entries)
        for line, e in zip(sec.lines, acct.entries, strict=True):
            assert line.posted_on == e.day and line.reference == e.ref
            assert line.amount_minor == (e.credit or e.debit)
            assert line.direction == (Direction.credit if e.credit else Direction.debit)
            assert line.balance_minor == e.balance
            if e.lines:
                assert line.description == _expected_desc(e)
        rec = compute_reconciliation(sec.kind, sec.opening_minor, sec.closing_minor, sec.lines)
        assert rec.status == "matched" and rec.diff_minor == 0


def test_summary_page_names_the_products():
    out = _parse(scenario_a())[1]
    assert [s.product for s in out.sections] == ["Neo Plus Saver - Example", "Neo Current Account"]


def test_page_break_uses_carry_rows_and_keeps_every_line():
    neo = scenario_a()
    doc, out = _parse(neo)
    assert doc.pages == 5  # summary + savings + current x2 + notes page
    assert "Balance carried forward" in doc.text and "Balance brought forward" in doc.text
    current = out.sections[1]
    assert len(current.lines) == 17
    # the carry rows are neither transactions nor a second opening/closing balance
    assert current.opening_minor == neo.accounts[1].opening


def test_three_decimal_amounts_become_two_decimal_minor_units():
    assert parse_money3("6,000.000") == 600000
    assert parse_money3("138.100") == 13810
    assert parse_money3("0.000") == 0
    assert parse_money3("12.34") is None and parse_money3("1,2.000") is None


def test_wrapped_description_is_assembled_from_lines_above_and_below_the_dated_row():
    entry = scenario_a().accounts[1].entries[0]
    line = _parse(scenario_a())[1].sections[1].lines[0]
    assert len(entry.lines) == 5 and line.description == _expected_desc(entry)
    assert line.description.startswith("Cardless Cash withdrawal") and line.description.endswith("100001")


def test_blank_description_falls_back_to_reference_family():
    out = _parse(scenario_b())[1]
    assert [ln.description for ln in out.sections[0].lines][0] == "Account to Account Transfer"


def test_filler_zero_lines_are_text_not_transactions():
    out = _parse(scenario_b())[1]
    remit = out.sections[1].lines[1]
    assert remit.amount_minor == 97500 and remit.direction == Direction.credit
    assert remit.description == "Inward Remittance SAMPLE SENDER NAME EXAMPLE BANK MIDDLE EAST LIMITED"
    assert len(out.sections[1].lines) == 6  # no phantom zero-amount rows


def test_ambiguous_gap_follows_the_centring_of_the_previous_row():
    """Five-line entry (two lines below its dated row) followed by a one-line entry: nothing leaks forward."""
    out = _parse(scenario_a())[1]
    cardless = next(ln for ln in out.sections[1].lines if "TESTATM2" in ln.description)
    assert cardless.description.endswith("20-AUG-26 100003")
    salary = out.sections[1].lines[[ln.reference for ln in out.sections[1].lines].index("033WPSP26ZZ16A16")]
    assert salary.description == "Salary Received"


def test_garbled_arabic_header_lines_are_ignored_but_english_labels_with_arabic_twin_are_kept():
    text = "\n".join(
        [
            "Consolidated Relationship Statement ¶·CVr¥˙",
            "TESTA EXAMPLE-HOLDER",
            "Issue Date 02/09/2026",
            "Current Account Statement (cid:237)(cid:209)(cid:226)M¥˙ ¨(cid:226)ZP¥˙",
            "Account No 019000000001 AED ¨(cid:226)ZP¥˙ §z(cid:209)",
            "IBAN AE070330000019000000001",
            "Statement for Period 01/08/2026 to 31/08/2026",
            "Date Transaction Reference no Debit Credit Balance",
            "Q‡(cid:209)(cid:226)G¥˙ ¶ƒ¤(cid:226)r'¥˙ pLV'¥˙ §z(cid:209) ¶›‡T¤",
            "OPENING BALANCE 0.000 0.000 100.000",
            "03/08/2026 Salary Received 033WPSP26ZZ99Z99 0.000 50.000 150.000",
            "CLOSING BALANCE 0.000 0.000 150.000",
            "Page 3 of 4",
        ]
    )
    sections, issue = parse_account_sections(text)
    assert issue == date(2026, 9, 2) and len(sections) == 1
    sec = sections[0]
    assert (sec.opening_minor, sec.closing_minor, sec.last4) == (10000, 15000, "0001")
    assert [(ln.amount_minor, ln.direction) for ln in sec.lines] == [(5000, Direction.credit)]


def test_running_balance_mismatch_is_reported_not_hidden():
    text = "\n".join(
        [
            "Current Account Statement",
            "Account No 019000000001 AED",
            "Statement for Period 01/08/2026 to 31/08/2026",
            "Date Transaction Reference no Debit Credit Balance",
            "OPENING BALANCE 0.000 0.000 100.000",
            "03/08/2026 Salary Received 033WPSP26ZZ99Z99 0.000 50.000 175.000",
            "CLOSING BALANCE 0.000 0.000 175.000",
        ]
    )
    (sec,), _ = parse_account_sections(text)
    assert sec.warnings == ["running balance does not match amount"]


def test_counterparties_on_statement_lines():
    out = _parse(scenario_a())[1]
    lines = out.sections[1].lines
    people = [
        (ln.counterparty.name, ln.counterparty.direction, ln.counterparty.iban_tail)
        for ln in lines
        if ln.counterparty
    ]
    assert people == [
        ("JANE Q SAMPLE", "outgoing", "0123"),
        ("BOB T EXAMPLE", "incoming", "0124"),
        ("Jane Q Sample", "outgoing", "0001"),
    ]
    # own account, card payments, cardless cash and salary never produce a counterparty
    assert sum(1 for ln in lines if ln.counterparty is None) == len(lines) - 3
    may = _parse(scenario_b())[1]
    assert [ln.counterparty.name for ln in may.sections[1].lines if ln.counterparty] == [
        "SAMPLE SENDER NAME",
        "Jane Q Sample",
    ]
