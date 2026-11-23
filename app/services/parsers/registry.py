from datetime import datetime

from app.services.parsers.base import ParsedTransaction, Parser
from app.services.parsers.generic import GenericParser


class ParserRegistry:
    def __init__(self, fallback: Parser | None = None) -> None:
        self._parsers: dict[str, Parser] = {}
        self.fallback: Parser | None = fallback

    def register(self, parser: Parser) -> None:
        self._parsers[parser.institution.lower()] = parser

    def get(self, institution: str | None) -> Parser | None:
        return self._parsers.get(institution.lower()) if institution else None

    @property
    def institutions(self) -> list[str]:
        return sorted(self._parsers)

    def resolve(self, institution: str | None, sender: str | None, text: str) -> Parser | None:
        """Pick a parser: explicit institution, then sender/text sniffing, then the fallback."""
        if (parser := self.get(institution)) is not None:
            return parser
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
        return parser.parse(text, received_at=received_at)


# Register real institution parsers here as they are written:
#     registry.register(EmiratesNBDParser())
registry = ParserRegistry(fallback=GenericParser())
