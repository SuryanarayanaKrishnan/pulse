"""Abu Dhabi Islamic Bank statements.

Documented layout: card with transaction + posting dates (first used), "Total Amount Due" /
"Minimum Payment Due"; account statements have a running balance.
"""

from app.services.parsers.statements.layout import Layout, LayoutStatementParser

CARD = Layout(has_balance_column=False)
ACCOUNT = Layout()

PARSER = LayoutStatementParser("adib", card=CARD, account=ACCOUNT)
