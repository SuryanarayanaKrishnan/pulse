"""Synthetic "consolidated relationship statement" PDFs (reportlab) in the layout of a UAE digital bank's
multi-account e-statement: summary page, then one section per account with the table
``Date | Transaction | Reference no | Debit | Credit | Balance``.

Everything here is invented (names, numbers, merchants, amounts). What is faithful is the *structure*:
three-decimal amounts, vertically centred multi-line descriptions (wrapped text above and below the dated
row), filler lines ending ``0.000 0.000 0.000``, ``OPENING``/``CLOSING BALANCE`` and carried/brought
forward rows at page breaks, a repeated page header, mirrored "e-statement" margin text and a block of
unreadable (symbol) text standing in for the Arabic column headings.
"""

import io
from dataclasses import dataclass, field
from datetime import date

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

HOLDER = "TESTA EXAMPLE-HOLDER"
FAKE_CCY = "AED"
DATE_X, DESC_X, REF_X = 51, 104, 277
DEBIT_R, CREDIT_R, BAL_R = 436, 500, 568
PITCH, ENTRY_GAP = 9.0, 3.0
TABLE_TOP = 295.0
PAGE_BOTTOM = 735.0
SYMBOLS = (
    "¶·¥¤ª«¬¢£¦¨©®°±²³µ¹º»¼½¾¿"  # stands in for the Arabic heading glyphs the real PDF extracts as noise
)


def m3(minor: int) -> str:
    return f"{minor / 100:,.3f}"


@dataclass
class Entry:
    day: date
    lines: list[str]  # description lines top to bottom
    ref: str
    debit: int = 0  # minor units
    credit: int = 0
    row_at: int | None = None  # which description line sits on the dated row (default: centred)
    zero_at: list[int] = field(default_factory=list)  # description lines that also carry "0.000 0.000 0.000"
    balance: int = 0  # filled by the builder


@dataclass
class Account:
    number: str
    iban: str
    kind: str  # "Current" | "Savings"
    product: str
    opening: int
    entries: list[Entry]
    avg_balance: int = 0
    rate: str = "0.00%"

    @property
    def closing(self) -> int:
        return self.opening + sum(e.credit - e.debit for e in self.entries)


@dataclass
class Neo:
    issue: date
    start: date
    end: date
    accounts: list[Account]
    pdf: bytes = b""


def _centered(e: Entry) -> int:
    return e.row_at if e.row_at is not None else (len(e.lines) - 1) // 2


def _entry_height(e: Entry) -> float:
    return max(len(e.lines), 1) * PITCH + ENTRY_GAP


def _header_block(c: canvas.Canvas, kind: str, acct: Account, neo: Neo) -> None:
    h = 842
    c.setFont("Helvetica-Bold", 9)
    c.drawString(38, h - 122, HOLDER)
    c.setFont("Helvetica", 8)
    for i, t in enumerate(("Example Holdings LLC", "OTHERS", "P.O.Box 0000 DUBAI,", "DUBAI")):
        c.drawString(38, h - 152 - i * 12.6, t)
    c.saveState()
    c.translate(18, h - 205)
    c.rotate(-90)
    c.setFont("Helvetica", 8)
    c.drawString(0, 0, "e - statement")
    c.restoreState()
    c.setFont("Helvetica-Bold", 13)
    c.drawString(283, h - 128, f"{kind} Account Statement")
    c.setFont("Helvetica", 7)
    c.drawString(480, h - 128, SYMBOLS[:10])
    c.setFont("Helvetica", 8)
    c.drawString(283, h - 163, "Account No")
    c.drawString(360, h - 163, f"{acct.number} {FAKE_CCY}")
    c.drawString(283, h - 183, "IBAN")
    c.drawString(360, h - 183, acct.iban)
    c.drawString(283, h - 202, "Customer Number")
    c.drawString(360, h - 202, "000000001")
    c.drawString(
        283, h - 222, "Mashreq NEO is part of Mashreqbank PSC a Bank that is regulated by the Central"
    )
    c.drawString(283, h - 231, "Bank of the United Arab Emirates.")
    c.setFont("Helvetica", 7)
    c.drawString(283, h - 242, SYMBOLS)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(
        49,
        h - 274,
        f"Statement for Period {neo.start.strftime('%d/%m/%Y')} to {neo.end.strftime('%d/%m/%Y')}",
    )
    c.setFont("Helvetica-Bold", 8)
    c.drawString(DATE_X, h - 295 + 0, "Date")
    c.drawString(DESC_X, h - 295, "Transaction")
    c.drawString(REF_X, h - 295, "Reference no")
    c.drawRightString(DEBIT_R, h - 295, "Debit")
    c.drawRightString(CREDIT_R, h - 295, "Credit")
    c.drawRightString(BAL_R, h - 295, "Balance")
    c.setFont("Helvetica", 7)
    c.drawString(DATE_X, h - 306, SYMBOLS[:8])
    c.drawString(DESC_X, h - 306, SYMBOLS[8:16])


def _footer(c: canvas.Canvas, n: int, total: int) -> None:
    c.setFont("Helvetica", 8)
    c.drawCentredString(300, 842 - 832, f"Page {n} of {total}")


def _summary_page(c: canvas.Canvas, neo: Neo) -> None:
    h = 842
    c.setFont("Helvetica-Bold", 14)
    c.drawString(283, h - 125, "Consolidated Relationship Statement")
    c.setFont("Helvetica", 7)
    c.drawString(500, h - 125, SYMBOLS[:6])
    c.setFont("Helvetica-Bold", 9)
    c.drawString(38, h - 137, HOLDER)
    c.setFont("Helvetica", 8)
    c.drawString(283, h - 140, "Customer Number")
    c.drawString(450, h - 140, "000000001")
    c.drawString(283, h - 160, "Issue Date")
    c.drawString(450, h - 160, neo.issue.strftime("%d/%m/%Y"))
    c.drawString(283, h - 194, "Statement Period")
    c.drawString(450, h - 194, f"{neo.start.strftime('%d/%m/%Y')} to {neo.end.strftime('%d/%m/%Y')}")
    for i, t in enumerate(("Example Holdings LLC", "OTHERS", "P.O.Box 0000 DUBAI,")):
        c.drawString(38, h - 152 - i * 12.6, t)
    c.drawString(
        283, h - 210, "Mashreq NEO is part of Mashreqbank PSC a Bank that is regulated by the Central Bank"
    )
    c.drawString(283, h - 219, "of the United Arab Emirates.")
    y = 275.0
    for kind in ("Current", "Savings"):
        accts = [a for a in neo.accounts if a.kind == kind]
        if not accts:
            continue
        c.setFont("Helvetica-Bold", 10)
        c.drawString(49, h - y, f"{kind} Account Summary")
        c.setFont("Helvetica-Bold", 7)
        y += 14
        c.drawString(106, h - y, "Foreign Currency")
        c.drawString(330, h - y, "Monthly Average")
        c.drawString(430, h - y, "Interest")
        y += 9
        for x, t in (
            (49, "Account Type"),
            (140, "Account Number"),
            (210, "Currency"),
            (300, "AED Balance"),
            (430, "Rate"),
        ):
            c.drawString(x, h - y, t)
        y += 9
        c.drawString(106, h - y, "Balance")
        c.drawString(330, h - y, "Balance")
        y += 14
        c.setFont("Helvetica", 8)
        for a in accts:
            words = a.product.split(" ")
            if kind == "Current" and len(words) >= 3:  # the real layout wraps the product name around the row
                c.drawString(49, h - y, " ".join(words[:2]))
                y += 11
                c.drawString(110, h - y, a.number)
                c.drawString(216, h - y, FAKE_CCY)
                c.drawRightString(270, h - y, "0.000")
                c.drawRightString(335, h - y, m3(a.closing))
                c.drawRightString(405, h - y, m3(a.avg_balance))
                c.drawRightString(450, h - y, "0.000")
                c.drawString(460, h - y, a.rate)
                y += 11
                c.drawString(49, h - y, " ".join(words[2:]))
            else:
                c.drawString(49, h - y, a.product)
                c.drawString(147, h - y, a.number)
                c.drawString(216, h - y, FAKE_CCY)
                c.drawRightString(270, h - y, "0.000")
                c.drawRightString(335, h - y, m3(a.closing))
                c.drawRightString(405, h - y, m3(a.avg_balance))
                c.drawString(460, h - y, a.rate)
            y += 14
            c.drawString(49, h - y, f"IBAN: {a.iban}")
            y += 28


def _howto_page(c: canvas.Canvas) -> None:
    h = 842
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, h - 60, " How to read/interpret your bank statement?")
    c.setFont("Helvetica", 8)
    text = [
        "Mashreqbank PSC bank statements contain the following information.",
        "Personal Information: Your Customer ID, Name and contact details are displayed on the first page of the",
        "statement.",
        "Statement Period: This is the first and last date of the period for which transactions are shown in the",
        "statement. These dates are displayed in DD/MM/YYYY format.",
        "Opening Balance: This is the amount you had in your account at the beginning of the statement period.",
        "Closing Balance: This is the amount in your account at the end of the statement period.",
        "Please note for foreign currency amounts AED balances are indicative only.",
    ]
    for i, t in enumerate(text):
        c.drawString(40, h - 80 - i * 11, t)
    c.setFont("Helvetica", 7)
    for i in range(6):
        c.drawString(60, h - 400 - i * 12, SYMBOLS[i : i + 14])


def build(neo: Neo, rows_per_page: float = PAGE_BOTTOM - TABLE_TOP) -> Neo:
    """Lay the accounts out on pages and render. Fills each entry's running balance."""
    pages: list[tuple[Account, list[Entry], bool, bool]] = []  # account, entries, first, last
    for acct in neo.accounts:
        bal = acct.opening
        for e in acct.entries:
            bal += e.credit - e.debit
            e.balance = bal
        chunk: list[Entry] = []
        used = 0.0
        out: list[list[Entry]] = []
        for e in acct.entries:
            if chunk and used + _entry_height(e) > rows_per_page - 3 * PITCH:
                out.append(chunk)
                chunk, used = [], 0.0
            chunk.append(e)
            used += _entry_height(e)
        out.append(chunk)
        for i, ch in enumerate(out):
            pages.append((acct, ch, i == 0, i == len(out) - 1))
    total = len(pages) + 2
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _summary_page(c, neo)
    _footer(c, 1, total)
    c.showPage()
    for pno, (acct, entries, first, last) in enumerate(pages, start=2):
        _header_block(c, acct.kind, acct, neo)
        top = TABLE_TOP + 16.0
        c.setFont("Helvetica", 8)

        def put_balance_row(label: str, bal: int, y: float) -> None:
            c.setFont("Helvetica", 8)
            c.drawString(DESC_X, 842 - y, label)
            c.drawRightString(DEBIT_R, 842 - y, "0.000")
            c.drawRightString(CREDIT_R, 842 - y, "0.000")
            c.drawRightString(BAL_R, 842 - y, m3(bal))

        prev_bal = acct.opening
        if first:
            put_balance_row("OPENING BALANCE", acct.opening, top)
        else:
            # brought forward = balance after the last row of the previous page
            idx = acct.entries.index(entries[0])
            prev_bal = acct.entries[idx - 1].balance if idx else acct.opening
            c.drawString(DESC_X, 842 - top, "Balance brought forward")
            c.drawRightString(BAL_R, 842 - top, m3(prev_bal))
        top += PITCH + 3
        for e in entries:
            row_i = _centered(e)
            for i, text in enumerate(e.lines):
                y = top + i * PITCH
                c.setFont("Helvetica", 8)
                if i == row_i:
                    c.drawString(DATE_X, 842 - (y + 2), e.day.strftime("%d/%m/%Y"))
                    c.drawString(REF_X, 842 - (y + 2), e.ref)
                    c.drawRightString(DEBIT_R, 842 - (y + 2), m3(e.debit))
                    c.drawRightString(CREDIT_R, 842 - (y + 2), m3(e.credit))
                    c.drawRightString(BAL_R, 842 - (y + 2), m3(e.balance))
                c.drawString(DESC_X, 842 - y, text)
                if i in e.zero_at:
                    c.drawRightString(DEBIT_R, 842 - (y + 2), "0.000")
                    c.drawRightString(CREDIT_R, 842 - (y + 2), "0.000")
                    c.drawRightString(BAL_R, 842 - (y + 2), "0.000")
            if not e.lines:
                y = top
                c.drawString(DATE_X, 842 - y, e.day.strftime("%d/%m/%Y"))
                c.drawString(REF_X, 842 - y, e.ref)
                c.drawRightString(DEBIT_R, 842 - y, m3(e.debit))
                c.drawRightString(CREDIT_R, 842 - y, m3(e.credit))
                c.drawRightString(BAL_R, 842 - y, m3(e.balance))
            top += _entry_height(e)
        if last:
            put_balance_row("CLOSING BALANCE", acct.closing, top + 6)
        else:
            c.drawString(DESC_X, 842 - top - 6, "Balance carried forward")
            c.drawRightString(BAL_R, 842 - top - 6, m3(entries[-1].balance))
        _footer(c, pno, total)
        c.showPage()
    _howto_page(c)
    _footer(c, total - 0, total)
    c.showPage()
    c.save()
    neo.pdf = buf.getvalue()
    return neo


def _d(day: int, month: int = 8) -> date:
    return date(2026, month, day)


A2A = "Account to Account Transfer"


def _a2a(src: str, dst: str, day: date, ref: str, *, debit: int = 0, credit: int = 0) -> Entry:
    return Entry(
        day, [A2A, f"FROM ACCOUNT No:{src} TO", f"ACCOUNT No: {dst}"], ref, debit=debit, credit=credit,
        row_at=0, zero_at=[1],
    )  # fmt: skip


def scenario_a() -> Neo:
    """Savings + current section; the current section spans two pages (carried/brought forward)."""
    cur_no, sav_no = "019000000001", "019000000002"
    cur_iban, sav_iban = "AE070330000019000000001", "AE070330000019000000002"
    person_iban = "AE450260001234567890123"
    card_a = "000000000000400000XXXXXX0001 -"
    card_b = "000000000000400000XXXXXX0002 -"
    cur = [
        Entry(_d(3), ["VALUE DATE: 02-08-2026 Cardless Cash", "withdrawal 1000000000000000000000001",
                      "TESTATM1 000000000001 0000001-EX", "COLLECTN TESTVILLE AE AED 100001",
                      "000001 02-AUG-26 100001"], "038CLCH26ZZ01A01", debit=20000),
        Entry(_d(5), ["VALUE DATE: 04-08-2026 IPP TRANSFER", card_a, "Testa - /REF/ Credit card payment -",
                      "T_00000000000000000000000000000a01"], "033IPPD26ZZ02A02", debit=31250),
        Entry(_d(5), ["VALUE DATE: 04-08-2026 Online Local Fund", "Transfer, Testa, A/C No:", "000000000001",
                      "EXAMPLE BANK, CARD PAYMENT"], "033DCRD26ZZ03A03", debit=4175, zero_at=[3]),
        Entry(_d(10), ["Salary Received"], "033WPSP26ZZ04A04", credit=1234500),
        Entry(_d(10), ["Cardless Cash withdrawal", "1000000000000000000000002 TESTATM1",
                       "000000000002 0000001-EX COLLECTN", "TESTVILLE AE AED 100002", "10-AUG-26 100002"],
              "038CLCH26ZZ05A05", debit=15000),
        Entry(_d(12), ["IPP TRANSFER", f"000000000000{sav_iban} -", "TESTA EXAMPLE-HOLDER - /REF/ Own",
                       "Account Transfer -", "T_00000000000000000000000000000a02"], "033IPPD26ZZ06A06", debit=67000),
        Entry(_d(12), ["IPP TRANSFER", f"000000000000{person_iban} -", "JANE Q SAMPLE - /REF/ Dinner split -",
                       "T_00000000000000000000000000000a03"], "033IPPD26ZZ07A07", debit=9250, row_at=1),
        Entry(_d(13), ["IPP TRANSFER", "000000000000AE890350001234567890124 -",
                       "BOB T EXAMPLE - /REF/ Dinner share -", "T_00000000000000000000000000000a04"],
              "033IPPD26ZZ08A08", credit=9250, row_at=1),
        Entry(_d(14), ["IPP TRANSFER", card_a, "Testa - /REF/ Credit card payment -",
                       "T_00000000000000000000000000000a05"], "033IPPD26ZZ09A09", debit=258300, row_at=1),
        Entry(_d(15), ["Online Local Fund Transfer, Testa,", "A/C No: 000000000001", "EXAMPLE BANK, CARD PAYMENT"],
              "033DCRD26ZZ10A10", debit=2125, row_at=0, zero_at=[2]),
        Entry(_d(16), ["QuickRemit 000000000009 to A/C", "900000000001 - Jane Q Sample With",
                       "Transaction Ref#MQXX00000000001"], "006FSBI26ZZ11A11", debit=54320),
        _a2a(cur_no, sav_no, _d(17), "033AACT26ZZ12A12", debit=123456),
        _a2a(cur_no, sav_no, _d(18), "033AACT26ZZ13A13", debit=345000),
        Entry(_d(19), ["VALUE DATE: 18-08-2026 IPP TRANSFER", card_b, "Testa - /REF/ Credit card payment -",
                       "T_00000000000000000000000000000a06"], "033IPPD26ZZ14A14", debit=7700, row_at=1),
        Entry(_d(20), ["Cardless Cash withdrawal", "1000000000000000000000003 TESTATM2",
                       "000000000003 0000002-EX COLLECTN", "TESTVILLE AE AED 100003", "20-AUG-26 100003"],
              "038CLCH26ZZ15A15", debit=11000),
        Entry(_d(22), ["Salary Received"], "033WPSP26ZZ16A16", credit=1234500),
        _a2a(sav_no, cur_no, _d(23), "033AACT26ZZ17A17", credit=55500),
    ]  # fmt: skip
    sav = [
        _a2a(cur_no, sav_no, _d(17), "033AACT26ZZ12A12", credit=123456),
        _a2a(cur_no, sav_no, _d(18), "033AACT26ZZ13A13", credit=345000),
        _a2a(sav_no, cur_no, _d(23), "033AACT26ZZ17A17", debit=55500),
        Entry(_d(31), ["VALUE DATE: 01-09-2026 Interest", sav_no], "018MCITAED 00007", credit=9876, row_at=0),
    ]
    neo = Neo(
        issue=date(2026, 9, 2),
        start=date(2026, 8, 1),
        end=date(2026, 8, 31),
        accounts=[
            Account(sav_no, sav_iban, "Savings", "Neo Plus Saver - Example", 912345, sav, 1012345, "4.50%"),
            Account(cur_no, cur_iban, "Current", "Neo Current Account", 271830, cur, 452222),
        ],
    )
    return build(neo)


def scenario_b() -> Neo:
    """Blank description row, inward remittance with sender/bank filler lines, wrapped "Account to Account"."""
    cur_no, sav_no = "019000000001", "019000000002"
    cur_iban, sav_iban = "AE070330000019000000001", "AE070330000019000000002"
    sav = [
        Entry(_d(1, 9), [], "033AACT26ZZ21B21", debit=321000),
        _a2a(sav_no, cur_no, _d(18, 9), "033AACT26ZZ22B22", debit=187500),
        Entry(
            _d(25, 9),
            [
                "VALUE DATE: 24-09-2026 Account to Account",
                "Transfer",
                f"FROM ACCOUNT No:{cur_no} TO",
                f"ACCOUNT No: {sav_no}",
            ],
            "033AACT26ZZ23B23",
            credit=654300,
            row_at=0,
            zero_at=[2],
        ),
    ]
    cur = [
        Entry(_d(1, 9), [], "033AACT26ZZ21B21", credit=321000),
        Entry(_d(4, 9), ["Inward Remittance", "SAMPLE SENDER NAME", "EXAMPLE BANK MIDDLE EAST LIMITED"],
              "033IWRT26ZZ24B24", credit=97500, row_at=0, zero_at=[1, 2]),
        Entry(_d(6, 9), ["QuickRemit 000000000009 to A/C", "900000000001 - Jane Q Sample With",
                         "Transaction Ref#MQXX00000000002"], "006FSBI26ZZ25B25", debit=214680),
        _a2a(sav_no, cur_no, _d(18, 9), "033AACT26ZZ22B22", credit=187500),
        Entry(_d(23, 9), ["Salary Received"], "033WPSP26ZZ26B26", credit=1234500),
        Entry(_d(25, 9), [A2A, f"FROM ACCOUNT No:{cur_no} TO", "ACCOUNT No: 019000000003",
                          "VALUE DATE: 24-09-2026 IPP TRANSFER"], "033AACT26ZZ27B27", debit=76543,
              row_at=0, zero_at=[1]),
    ]  # fmt: skip
    neo = Neo(
        issue=date(2026, 9, 30),
        start=date(2026, 9, 1),
        end=date(2026, 9, 30),
        accounts=[
            Account(sav_no, sav_iban, "Savings", "Neo Plus Saver - Example", 2468000, sav, 2300000, "4.50%"),
            Account(cur_no, cur_iban, "Current", "Neo Current Account", 98765, cur, 477123),
        ],
    )
    return build(neo)
