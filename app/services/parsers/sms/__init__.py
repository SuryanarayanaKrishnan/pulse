"""One module per bank; each exposes ``PARSER``. Register new banks in ``ALL``."""

from app.services.parsers.sms import (
    adcb,
    adib,
    cbd,
    citi,
    dib,
    eib,
    enbd,
    fab,
    hsbc,
    liv,
    mashreq,
    rakbank,
)
from app.services.parsers.sms.engine import SmsParser

# Order matters for text sniffing: specific brands first (Emirates Islamic/Liv before Emirates NBD).
ALL: tuple[SmsParser, ...] = (
    eib.PARSER,
    liv.PARSER,
    enbd.PARSER,
    adcb.PARSER,
    adib.PARSER,
    fab.PARSER,
    mashreq.PARSER,
    rakbank.PARSER,
    dib.PARSER,
    hsbc.PARSER,
    citi.PARSER,
    cbd.PARSER,
)

__all__ = ["ALL", "SmsParser"]
