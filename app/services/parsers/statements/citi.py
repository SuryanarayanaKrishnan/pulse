"""Citi UAE statements.

Documented layout: card summary "Previous Balance" ... "New Balance" / "Minimum Payment Due"; rows have a
transaction date and a posting date (first used); credits carry "CR".
"""

from app.services.parsers.statements.layout import Labels, Layout, LayoutStatementParser

CARD = Layout(
    has_balance_column=False, labels=Labels(closing=(r"new balance", r"total amount due", r"closing balance"))
)
ACCOUNT = Layout()

PARSER = LayoutStatementParser("citi", card=CARD, account=ACCOUNT)
