"""Emirates NBD statements.

Documented layout (adjust ``CARD``/``ACCOUNT`` below when a real statement differs):
  card:    two dates per row (transaction, posting) - the transaction date is used; foreign amount is
           printed before the AED amount; credits carry "Cr"; summary labels "Previous Balance",
           "Total Amount Due", "Minimum Payment Due".
  account: "Date / Description / Debit-Credit amount / Balance" with a running balance.
"""

from app.services.parsers.statements.layout import Labels, Layout, LayoutStatementParser

CARD = Layout(has_balance_column=False)
ACCOUNT = Layout(labels=Labels(opening=(r"opening balance", r"balance brought forward")))

PARSER = LayoutStatementParser("enbd", card=CARD, account=ACCOUNT)
