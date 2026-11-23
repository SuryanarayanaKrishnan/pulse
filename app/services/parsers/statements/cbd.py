"""Commercial Bank of Dubai statements.

Documented layout: card summary "Previous Balance" ... "Total Amount Due"; one date per row; credits carry
"Cr". Account statements have a running balance.
"""

from app.services.parsers.statements.layout import Layout, LayoutStatementParser

CARD = Layout(has_balance_column=False)
ACCOUNT = Layout()

PARSER = LayoutStatementParser("cbd", card=CARD, account=ACCOUNT)
