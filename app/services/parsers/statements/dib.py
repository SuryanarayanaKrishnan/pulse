"""Dubai Islamic Bank statements (Islamic card: "profit"/"charges" instead of interest).

Documented layout: card with one date per row and "CR" markers; account statements have a running
balance and use dd/mm/yyyy dates.
"""

from app.services.parsers.statements.layout import Layout, LayoutStatementParser

CARD = Layout(has_balance_column=False)
ACCOUNT = Layout()

PARSER = LayoutStatementParser("dib", card=CARD, account=ACCOUNT)
