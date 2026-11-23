"""Liv (digital bank) statements. Account statements only; ISO-style or dd MMM yyyy dates and a running balance."""

from app.services.parsers.statements.layout import Layout, LayoutStatementParser

CARD = Layout(has_balance_column=False)
ACCOUNT = Layout()

PARSER = LayoutStatementParser("liv", card=CARD, account=ACCOUNT)
