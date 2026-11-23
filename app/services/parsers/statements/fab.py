"""First Abu Dhabi Bank statements.

Documented layout: card rows carry transaction and posting dates, the posting date is the one that
matches the statement cycle (``use_second_date``); summary uses "Previous Statement Balance" and
"Total Amount Due". Account statements have a running balance.
"""

from app.services.parsers.statements.layout import Layout, LayoutStatementParser

CARD = Layout(has_balance_column=False, use_second_date=True)
ACCOUNT = Layout()

PARSER = LayoutStatementParser("fab", card=CARD, account=ACCOUNT)
