"""Mashreq statements.

Documented layout: card credits are printed as negative amounts ("-500.00") instead of "Cr"; summary
labels "Previous Balance" / "Statement Balance". Account statements have a running balance.
"""

from app.services.parsers.statements.layout import Labels, Layout, LayoutStatementParser

CARD = Layout(
    has_balance_column=False,
    labels=Labels(closing=(r"statement balance", r"total amount due", r"closing balance")),
)
ACCOUNT = Layout()

PARSER = LayoutStatementParser("mashreq", card=CARD, account=ACCOUNT)
