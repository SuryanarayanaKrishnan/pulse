"""Emirates Islamic statements.

Documented layout: card with transaction + posting dates (first used), "Total Amount Due" / "Minimum
Payment Due", credits carry "Cr"; account statements have a running balance.
"""

from app.services.parsers.statements.layout import Layout, LayoutStatementParser

CARD = Layout(has_balance_column=False)
ACCOUNT = Layout()

PARSER = LayoutStatementParser("eib", card=CARD, account=ACCOUNT)
