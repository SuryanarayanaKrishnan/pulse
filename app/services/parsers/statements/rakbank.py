"""RAKBANK statements.

Documented layout: card summary "Previous Balance" ... "Total Outstanding"; AED amount first, then the
original foreign amount ("36.73 USD 10.00") on foreign spend rows (``foreign_before_local=False``).
"""

from app.services.parsers.statements.layout import Layout, LayoutStatementParser

CARD = Layout(has_balance_column=False, foreign_before_local=False)
ACCOUNT = Layout()

PARSER = LayoutStatementParser("rakbank", card=CARD, account=ACCOUNT)
