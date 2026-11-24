from dataclasses import replace
from datetime import datetime

from app.services.institutions import institutions
from app.services.parsers.base import ParsedTransaction, Parser
from app.services.parsers.generic import GenericParser
from app.services.parsers.sms import ALL as BANK_SMS_PARSERS


class ParserRegistry:
    def __init__(self, fallback: Parser | None = None) -> None:
        self._parsers: dict[str, Parser] = {}
        self.fallback: Parser | None = fallback

    def register(self, parser: Parser) -> None:
        self._parsers[parser.institution.lower()] = parser

    def get(self, institution: str | None) -> Parser | None:
        if not institution:
            return None
        if (parser := self._parsers.get(institution.lower())) is not None:
            return parser
        key = institutions.resolve_key(institution)  # "Emirates NBD" -> "enbd"
        return self._parsers.get(key) if key else None

    @property
    def institutions(self) -> list[str]:
        return sorted(self._parsers)

    def resolve(self, institution: str | None, sender: str | None, text: str) -> Parser | None:
        """Pick a parser: explicit institution, then sender/text sniffing, then the fallback."""
        if (parser := self.get(institution)) is not None:
            return parser
        # Sender IDs are the reliable signal; only then sniff names in the body.
        if sender and (key := institutions.from_sender(sender)) and key in self._parsers:
            return self._parsers[key]
        for candidate in self._parsers.values():
            if candidate.matches(sender, text):
                return candidate
        return self.fallback

    def parse(
        self,
        text: str,
        *,
        institution: str | None = None,
        sender: str | None = None,
        received_at: datetime | None = None,
    ) -> ParsedTransaction | None:
        parser = self.resolve(institution, sender, text)
        if parser is None:
            return None
        parsed = parser.parse(text, received_at=received_at)
        if parsed is None and self.fallback is not None and parser is not self.fallback:
            # A bank parser that does not recognise the wording must not hide a transaction the
            # generic heuristics can still read (it returns None for OTPs and non-transactions).
            parsed = self.fallback.parse(text, received_at=received_at)
            if parsed is not None and parser.institution:
                parsed = replace(parsed, institution=parser.institution)
        return parsed


registry = ParserRegistry(fallback=GenericParser())
for _parser in BANK_SMS_PARSERS:
    registry.register(_parser)
