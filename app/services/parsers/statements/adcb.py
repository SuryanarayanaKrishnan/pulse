"""ADCB statements.

Documented layout: card summary is a header row of labels with a row of values underneath
("Previous Balance  Payments  Purchases  Total Amount Due  Minimum Payment"); one date per row;
credits carry "CR". Account statements have a running balance.
"""

from app.services.parsers.statements.layout import Layout, LayoutStatementParser

CARD = Layout(has_balance_column=False, summary_table=True)
ACCOUNT = Layout()

PARSER = LayoutStatementParser("adcb", card=CARD, account=ACCOUNT)
