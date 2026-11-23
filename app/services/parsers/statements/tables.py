"""Header-driven table heuristic: reads statements from PDF tables or CSV rows without bank knowledge.

A header row is any row with a date column, a description-like column and at least one amount-like
column (amount / debit / credit / withdrawal / deposit / balance). Columns are mapped by name, so
column order and extra columns do not matter. Direction comes, in order of trust, from separate
debit/credit columns, the running-balance delta, an explicit Cr/Dr marker, and finally the sign
convention (bank: negative = debit; card: negative = credit, positive = purchase).
"""

import re
from dataclasses import dataclass, field

from app.models.transaction import Direction
from app.services.parsers.dates import parse_date
from app.services.parsers.statement_base import (
    ParsedStatement,
    StatementDocument,
    StatementKind,
    StatementLineData,
)
from app.services.parsers.statements.layout import Layout, detect_kind, extract_summary, finish
from app.services.parsers.statements.money import parse_money

_HEADER_SYNONYMS: dict[str, tuple[str, ...]] = {
    "date": ("transaction date", "trans date", "trans. date", "txn date", "date", "tran date"),
    "posting": ("posting date", "post date", "value date", "posted date", "booking date"),
    "desc": (
        "description",
        "details",
        "narration",
        "particulars",
        "transaction details",
        "merchant",
        "narrative",
    ),
    "debit": ("debit", "debits", "withdrawal", "withdrawals", "paid out", "money out", "dr", "dr."),
    "credit": ("credit", "credits", "deposit", "deposits", "paid in", "money in", "cr", "cr."),
    "amount": ("amount", "transaction amount", "amount (aed)", "amount aed", "amount in aed", "aed amount"),
    "balance": ("balance", "running balance", "closing balance"),
    "foreign_amount": ("foreign amount", "original amount", "foreign currency amount", "fx amount"),
    "foreign_ccy": ("foreign currency", "original currency", "fx currency", "currency"),
}


def _cell(v: str | None) -> str:
    return re.sub(r"\s+", " ", (v or "").replace("\n", " ")).strip()


def _classify(header: str) -> str | None:
    h = _cell(header).lower()
    if not h:
        return None
    h_plain = re.sub(r"\s*\([^)]*\)", "", h).strip()  # "Debit (AED)" -> "debit"
    for name, synonyms in _HEADER_SYNONYMS.items():
        if h in synonyms or h_plain in synonyms:
            return name
    return None


@dataclass
class _Mapping:
    columns: dict[str, int] = field(default_factory=dict)
    width: int = 0


def _header_mapping(row: list[str | None]) -> _Mapping | None:
    cols: dict[str, int] = {}
    for i, cell in enumerate(row):
        name = _classify(cell or "")
        if name and name not in cols:
            cols[name] = i
    if "date" in cols and "desc" in cols and (cols.keys() & {"amount", "debit", "credit", "balance"}):
        return _Mapping(cols, len(row))
    return None


def _signed_balance(token: str) -> int | None:
    m = parse_money(token)
    if m is None:
        return None
    return -m.minor if (m.marker == "DR" or m.negative) else m.minor


def parse_tables(doc: StatementDocument, kind: StatementKind | None = None) -> ParsedStatement | None:
    if kind is None:
        kind = detect_kind(doc.text) if doc.text.strip() else "bank_account"
    stmt = ParsedStatement(institution=None, kind=kind, parser=f"tables:{kind}")
    layout = Layout(has_balance_column=True)
    if doc.text.strip():
        extract_summary([ln.strip() for ln in doc.text.splitlines() if ln.strip()], stmt, layout)

    mapping: _Mapping | None = None
    prev_balance = stmt.opening_minor if kind == "bank_account" else None
    for table in doc.tables:
        for row in table:
            if not any(_cell(c) for c in row):
                continue
            if (hm := _header_mapping(row)) is not None:
                mapping = hm
                continue
            if mapping is None or len(row) < mapping.width - 1:
                continue
            line, prev_balance = _row(row, mapping, kind, prev_balance, stmt)
            if line is not None:
                stmt.lines.append(line)

    if not stmt.lines:
        return None
    _infer_balances_from_column(stmt)
    finish(stmt)
    return stmt


def _get(row: list[str | None], mapping: _Mapping, name: str) -> str:
    i = mapping.columns.get(name)
    return _cell(row[i]) if i is not None and i < len(row) else ""


def _row(
    row: list[str | None], mp: _Mapping, kind: StatementKind, prev_balance: int | None, stmt: ParsedStatement
) -> tuple[StatementLineData | None, int | None]:
    posted = parse_date(_get(row, mp, "date")) or parse_date(_get(row, mp, "posting"))
    desc = _get(row, mp, "desc")
    if posted is None or not desc:
        return None, prev_balance
    balance = _signed_balance(_get(row, mp, "balance")) if "balance" in mp.columns else None

    debit, credit, amount = (
        parse_money(_get(row, mp, "debit")),
        parse_money(_get(row, mp, "credit")),
        parse_money(_get(row, mp, "amount")),
    )
    direction: Direction | None = None
    chosen = None
    if debit and debit.minor and not (credit and credit.minor):
        direction, chosen = Direction.debit, debit
    elif credit and credit.minor and not (debit and debit.minor):
        direction, chosen = Direction.credit, credit
    elif amount and amount.minor:
        chosen = amount
        if balance is not None and prev_balance is not None and abs(balance - prev_balance) == amount.minor:
            direction = Direction.credit if balance > prev_balance else Direction.debit
        elif amount.marker == "CR":
            direction = Direction.credit
        elif amount.marker == "DR":
            direction = Direction.debit
        elif kind == "credit_card":
            direction = Direction.credit if amount.negative else Direction.debit
        else:
            direction = Direction.debit if amount.negative else Direction.credit
            stmt.warnings.append(
                "bank CSV/table amounts without markers: assumed negative=debit, positive=credit"
            )
    if chosen is None or direction is None:
        return None, prev_balance

    fa = parse_money(_get(row, mp, "foreign_amount"))
    fccy = _get(row, mp, "foreign_ccy").upper() or None
    if fccy and (len(fccy) != 3 or fccy == stmt.currency):
        fccy = None
    line = StatementLineData(
        posted_on=posted,
        description=desc,
        amount_minor=chosen.minor,
        direction=direction,
        currency=stmt.currency,
        foreign_amount_minor=fa.minor if fa and fccy else None,
        foreign_currency=fccy if fa else None,
        balance_minor=balance,
    )
    return line, balance if balance is not None else prev_balance


def _infer_balances_from_column(stmt: ParsedStatement) -> None:
    """No summary block (CSV): derive opening/closing from the running-balance column."""
    with_bal = [ln for ln in stmt.lines if ln.balance_minor is not None]
    if not with_bal or stmt.kind != "bank_account":
        return
    first, last = stmt.lines[0], stmt.lines[-1]
    if stmt.closing_minor is None and last.balance_minor is not None:
        stmt.closing_minor = last.balance_minor
    if stmt.opening_minor is None and first.balance_minor is not None:
        delta = first.amount_minor if first.direction is Direction.credit else -first.amount_minor
        stmt.opening_minor = first.balance_minor - delta
