"""Synthetic statement PDFs/CSVs (reportlab) following each bank's documented layout.

The expected values are computed alongside the text so tests assert against ground truth.
"""

import io
from dataclasses import dataclass, field
from datetime import date, timedelta

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

BASE = date(2026, 3, 1)


def fmt_money(minor: int) -> str:
    return f"{minor / 100:,.2f}"


@dataclass
class Style:
    key: str
    header: str  # text that identifies the bank in the PDF header
    date_fmt: str = "%d %b %Y"
    two_dates: bool = False
    posted_is_second: bool = False
    credit: str = "Cr"  # "Cr" | "CR" | "-"
    foreign_first: bool = True
    closing_label: str = "Total Amount Due"
    opening_label: str = "Previous Balance"
    summary_table: bool = False
    has_card: bool = True


STYLES: dict[str, Style] = {
    s.key: s
    for s in (
        Style("enbd", "EMIRATES NBD", "%d %b %Y", two_dates=True),
        Style("adcb", "ABU DHABI COMMERCIAL BANK", "%d/%m/%Y", credit="CR", summary_table=True),
        Style(
            "fab",
            "FIRST ABU DHABI BANK",
            "%d-%b-%Y",
            two_dates=True,
            posted_is_second=True,
            opening_label="Previous Statement Balance",
        ),
        Style("mashreq", "MASHREQ", "%d/%m/%Y", credit="-", closing_label="Statement Balance"),
        Style("rakbank", "RAKBANK", "%d-%b-%y", foreign_first=False, closing_label="Total Outstanding"),
        Style("dib", "DUBAI ISLAMIC BANK", "%d/%m/%Y", credit="CR"),
        Style("adib", "ABU DHABI ISLAMIC BANK", "%d %b %Y", two_dates=True),
        Style("hsbc", "HSBC", "%d %b %Y", credit="CR", closing_label="Statement Balance"),
        Style("citi", "CITIBANK", "%d %b %Y", two_dates=True, credit="CR", closing_label="New Balance"),
        Style("cbd", "COMMERCIAL BANK OF DUBAI", "%d-%m-%Y"),
        Style("eib", "EMIRATES ISLAMIC", "%d %b %Y", two_dates=True),
        Style("liv", "LIV", "%Y-%m-%d", has_card=False),
    )
}


@dataclass
class Txn:
    day: int  # day of March 2026
    desc: str
    amount: int  # minor, positive
    credit: bool = False
    foreign: tuple[str, int] | None = None
    wrap: str | None = None  # wrapped second line of the description


CARD_TXNS = [
    Txn(2, "CARREFOUR DUBAI AE", 12050),
    Txn(5, "PAYMENT RECEIVED - THANK YOU", 50000, credit=True),
    Txn(7, "AMAZON US SEATTLE US", 3673, foreign=("USD", 1000)),
    Txn(10, "TALABAT DUBAI AE", 8525),
    Txn(12, "NOON.COM REFUND", 4000, credit=True),
    Txn(15, "UBER *TRIP HELP.UBER.COM", 6200, wrap="AMSTERDAM NL"),
    Txn(18, "ENOC STATION 1234 DUBAI AE", 15000),
]
CARD_OPENING = 100000

ACCOUNT_TXNS = [
    Txn(1, "POS PURCHASE CARREFOUR DUBAI", 12050),
    Txn(3, "SALARY CREDIT ACME LLC", 1500000, credit=True),
    Txn(6, "ATM CASH WITHDRAWAL DUBAI MALL", 50000),
    Txn(9, "ENOC STATION 1234", 15000),
    Txn(14, "IPI TRANSFER FROM AHMED", 20000, credit=True),
    Txn(20, "DEWA BILL PAYMENT", 43210),
]
ACCOUNT_OPENING = 500000


@dataclass
class Built:
    lines: list[str]
    txns: list[Txn]
    style: Style
    kind: str
    opening: int
    closing: int
    min_due: int | None = None
    last4: str = "1234"
    posted: list[date] = field(default_factory=list)


def _d(style: Style, day: int, offset: int = 0) -> str:
    return (BASE + timedelta(days=day - 1 + offset)).strftime(style.date_fmt).replace(" 0", " 0")


def build_card(style: Style, txns: list[Txn] | None = None, opening: int = CARD_OPENING) -> Built:
    txns = txns if txns is not None else CARD_TXNS
    debits = sum(t.amount for t in txns if not t.credit)
    credits_ = sum(t.amount for t in txns if t.credit)
    closing = opening + debits - credits_
    min_due = max(10000, closing // 20)
    start, end = BASE - timedelta(days=4), BASE + timedelta(days=24)  # 25 Feb .. 25 Mar
    lines = [
        f"{style.header}",
        "Credit Card Statement",
        "Card Number: 4111 11XX XXXX 1234",
        f"Statement Period: {start.strftime(style.date_fmt)} to {end.strftime(style.date_fmt)}",
        f"Statement Date: {end.strftime(style.date_fmt)}",
        f"Payment Due Date: {(end + timedelta(days=25)).strftime(style.date_fmt)}",
        "Credit Limit AED 20,000.00",
    ]
    if style.summary_table:
        lines += [
            f"{style.opening_label} Payments Purchases {style.closing_label} Minimum Payment Due",
            f"{fmt_money(opening)} {fmt_money(credits_)} {fmt_money(debits)} {fmt_money(closing)} {fmt_money(min_due)}",
        ]
    else:
        lines += [
            f"{style.opening_label} {fmt_money(opening)}",
            f"Payments / Credits {fmt_money(credits_)}",
            f"Purchases / Charges {fmt_money(debits)}",
            f"{style.closing_label} AED {fmt_money(closing)}",
            f"Minimum Payment Due {fmt_money(min_due)}",
        ]
    lines += ["", "Date Description Amount (AED)"]
    posted: list[date] = []
    for t in txns:
        dstr = _d(style, t.day)
        if style.two_dates:
            dstr += " " + _d(style, t.day, 1)
        posted.append(BASE + timedelta(days=t.day - 1 + (1 if style.posted_is_second else 0)))
        amt = fmt_money(t.amount)
        if t.credit:
            amt = f"-{amt}" if style.credit == "-" else f"{amt} {style.credit}"
        mid = ""
        if t.foreign:
            f = f"{t.foreign[0]} {fmt_money(t.foreign[1])}"
            mid = f"{f} {amt}" if style.foreign_first else f"{amt} {f}"
            lines.append(f"{dstr} {t.desc} {mid}")
        else:
            lines.append(f"{dstr} {t.desc} {amt}")
        if t.wrap:
            lines.append(t.wrap)
    lines += ["", f"Total {fmt_money(debits)}", "Page 1 of 1"]
    return Built(lines, txns, style, "credit_card", opening, closing, min_due, posted=posted)


def build_account(style: Style, txns: list[Txn] | None = None, opening: int = ACCOUNT_OPENING) -> Built:
    txns = txns if txns is not None else ACCOUNT_TXNS
    bal = opening
    for t in txns:
        bal += t.amount if t.credit else -t.amount
    closing = bal
    start, end = BASE, date(2026, 3, 31)
    lines = [
        style.header,
        "Account Statement",
        "Account Number: 1234567890123",
        "IBAN: AE07 0331 2345 6789 0123 456",
        f"Statement Period: {start.strftime(style.date_fmt)} to {end.strftime(style.date_fmt)}",
        f"Opening Balance {fmt_money(opening)}",
        f"Closing Balance {fmt_money(closing)}",
        "",
        "Date Description Amount Balance",
    ]
    bal, posted = opening, []
    for t in txns:
        bal += t.amount if t.credit else -t.amount
        lines.append(f"{_d(style, t.day)} {t.desc} {fmt_money(t.amount)} {fmt_money(bal)}")
        posted.append(BASE + timedelta(days=t.day - 1))
    lines += ["", "Page 1 of 1"]
    return Built(lines, txns, style, "bank_account", opening, closing, last4="0123", posted=posted)


def render_pdf(lines: list[str], password: str | None = None, per_page: int = 55) -> bytes:
    buf = io.BytesIO()
    kw = {"encrypt": password} if password else {}
    c = canvas.Canvas(buf, pagesize=A4, **kw)
    y_start = 800
    y = y_start
    for i, line in enumerate(lines):
        if i and i % per_page == 0:
            c.showPage()
            y = y_start
        c.setFont("Courier", 8)
        c.drawString(30, y, line)
        y -= 11
    c.save()
    return buf.getvalue()


def render_table_pdf(preamble: list[str], header: list[str], rows: list[list[str]]) -> bytes:
    """A ruled table (platypus) so pdfplumber's lattice table extraction finds it."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph

    styles = getSampleStyleSheet()
    flow = [Paragraph(p, styles["Normal"]) for p in preamble] + [Spacer(1, 12)]
    t = Table([header, *rows], repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("FONTSIZE", (0, 0), (-1, -1), 8)]))
    flow.append(t)
    doc.build(flow)
    return buf.getvalue()
