"""Registry of supported UAE institutions: names/aliases, SMS sender IDs, statement-email sources.

This is the one place to adjust when a bank changes its sender address, SMS sender ID or wording.
The email domains and subject terms below are best-effort defaults (no real mailbox was available
when this was written): verify them against a real statement email and tweak.
"""

import re
from dataclasses import dataclass, field

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def norm_name(value: str) -> str:
    return _NON_ALNUM.sub("", value.lower())


@dataclass(frozen=True, slots=True)
class Institution:
    key: str  # canonical key stored on statements / used as the parser id
    name: str
    aliases: tuple[str, ...] = ()
    sms_senders: tuple[str, ...] = ()
    # Gmail: statements come from these domains (matched with `from:`)
    email_domains: tuple[str, ...] = ()
    subject_terms: tuple[str, ...] = ("statement", "e-statement", "estatement")
    # Text found in the statement PDF / SMS body; matched case-insensitively on word boundaries.
    text_markers: tuple[str, ...] = ()
    extra_query: str = ""  # appended verbatim to the Gmail search, e.g. 'filename:pdf'

    def all_names(self) -> tuple[str, ...]:
        return (self.key, self.name, *self.aliases)


@dataclass
class InstitutionRegistry:
    _items: dict[str, Institution] = field(default_factory=dict)

    def register(self, inst: Institution) -> None:
        self._items[inst.key] = inst

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._items.values())

    def get(self, key: str) -> Institution | None:
        return self._items.get(key)

    @property
    def keys(self) -> list[str]:
        return list(self._items)

    def resolve_key(self, name: str | None) -> str | None:
        """Map a free-text institution/alias ("Emirates NBD", "enbd", "ENBD Bank") to its key."""
        if not name or not name.strip():
            return None
        n = norm_name(name)
        for inst in self._items.values():
            if n in {norm_name(x) for x in inst.all_names()}:
                return inst.key
        # "Emirates NBD Credit Card" style: alias contained in the string (longest alias wins).
        best: tuple[int, str] | None = None
        for inst in self._items.values():
            for alias in inst.all_names():
                a = norm_name(alias)
                if len(a) >= 4 and a in n and (best is None or len(a) > best[0]):
                    best = (len(a), inst.key)
        return best[1] if best else None

    def from_sender(self, sender: str | None) -> str | None:
        if not sender:
            return None
        n = norm_name(sender)
        for inst in self._items.values():
            if n in {norm_name(s) for s in inst.sms_senders}:
                return inst.key
        return None

    def from_email_address(self, address: str | None) -> str | None:
        if not address or "@" not in address:
            return None
        domain = address.rsplit("@", 1)[1].strip(" >").lower()
        for inst in self._items.values():
            if any(domain == d or domain.endswith("." + d) for d in inst.email_domains):
                return inst.key
        return None

    def detect_in_text(self, text: str, head_chars: int = 2500) -> str | None:
        """Institution whose marker appears earliest in the head of the text (headers carry the logo/name)."""
        head = text[:head_chars]
        best: tuple[int, str] | None = None
        for inst in self._items.values():
            for marker in inst.text_markers:
                m = re.search(rf"(?<![A-Za-z0-9]){re.escape(marker)}(?![A-Za-z0-9])", head, re.IGNORECASE)
                if m and (best is None or m.start() < best[0]):
                    best = (m.start(), inst.key)
        return best[1] if best else None


institutions = InstitutionRegistry()

for _inst in (
    Institution(
        "enbd",
        "Emirates NBD",
        ("ENBD", "Emirates National Bank of Dubai"),
        ("ENBD", "EmiratesNBD", "Emirates NBD"),
        ("emiratesnbd.com",),
        text_markers=("EMIRATES NBD", "EMIRATESNBD.COM", "ENBD"),
    ),
    Institution(
        "adcb",
        "ADCB",
        ("Abu Dhabi Commercial Bank",),
        ("ADCB",),
        ("adcb.com",),
        text_markers=("ABU DHABI COMMERCIAL BANK", "ADCB"),
    ),
    Institution(
        "fab",
        "First Abu Dhabi Bank",
        ("FAB", "Bank FAB", "BankFAB"),
        ("FAB", "BankFAB"),
        ("bankfab.com",),
        text_markers=("FIRST ABU DHABI BANK", "BANKFAB.COM", "BANK FAB"),
    ),
    Institution(
        "mashreq",
        "Mashreq",
        ("Mashreq Bank", "MashreqBank"),
        ("MASHREQ", "Mashreq"),
        ("mashreq.com", "mashreqbank.com"),
        text_markers=("MASHREQ", "MASHREQBANK"),
    ),
    Institution(
        "rakbank",
        "RAKBANK",
        ("RAK Bank", "National Bank of Ras Al Khaimah"),
        ("RAKBANK", "RAK BANK"),
        ("rakbank.ae",),
        text_markers=("RAKBANK", "RAK BANK"),
    ),
    Institution(
        "dib",
        "Dubai Islamic Bank",
        ("DIB",),
        ("DIB", "DubaiIslamic", "DubaiIslamicBank"),
        ("dib.ae",),
        text_markers=("DUBAI ISLAMIC BANK", "DIB"),
    ),
    Institution(
        "adib",
        "Abu Dhabi Islamic Bank",
        ("ADIB",),
        ("ADIB",),
        ("adib.ae", "adib.com"),
        text_markers=("ABU DHABI ISLAMIC BANK", "ADIB"),
    ),
    Institution(
        "hsbc",
        "HSBC UAE",
        ("HSBC", "HSBC Bank Middle East"),
        ("HSBC", "HSBCUAE"),
        ("hsbc.ae", "hsbc.com", "mail.hsbc.com"),
        text_markers=("HSBC",),
    ),
    Institution(
        "citi",
        "Citi UAE",
        ("Citi", "Citibank", "Citibank UAE"),
        ("Citibank", "CITI", "Citi"),
        ("citi.com", "citibank.com"),
        text_markers=("CITIBANK", "CITI"),
    ),
    Institution(
        "cbd",
        "Commercial Bank of Dubai",
        ("CBD",),
        ("CBD", "CBDUAE"),
        ("cbd.ae",),
        text_markers=("COMMERCIAL BANK OF DUBAI", "CBD"),
    ),
    Institution(
        "eib",
        "Emirates Islamic",
        ("EIB", "Emirates Islamic Bank"),
        ("EmiratesIslamic", "EIB", "E-Islamic"),
        ("emiratesislamic.ae",),
        text_markers=("EMIRATES ISLAMIC",),
    ),
    Institution(
        "liv",
        "Liv",
        ("Liv.", "Liv by Emirates NBD"),
        ("LIV", "Liv."),
        ("liv.me",),
        text_markers=("LIV.ME", "LIV BANKING", "LIV"),
    ),
):
    institutions.register(_inst)
