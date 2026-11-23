"""HSBC UAE statements.

Documented layout: card summary "Previous Statement Balance" ... "Statement Balance" / "Minimum Payment
Due"; credits carry "CR"; account statements have a running balance.
"""

from app.services.parsers.statements.layout import Labels, Layout, LayoutStatementParser

CARD = Layout(
    has_balance_column=False,
    labels=Labels(closing=(r"statement balance", r"total amount due", r"closing balance")),
)
ACCOUNT = Layout()

PARSER = LayoutStatementParser("hsbc", card=CARD, account=ACCOUNT)
