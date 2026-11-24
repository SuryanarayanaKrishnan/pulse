"""Card face art: deterministic generation, hand overrides and validation.

``generate(card)`` builds a plausible, readable face for any catalog card from three inputs: the issuer's
colour family, the tier (which picks finish and how dark the face runs) and the product kind (which picks
the motif). No randomness: the same card always gets the same art. ``OVERRIDES`` holds what we know of
specific real products (Skywards, noon, Amex Platinum...). ``scripts/build_card_art.py`` writes the result to
``data/cards/art.json``, which is data: edit it by hand to correct a card, and ``--check`` only reports drift
for cards that have no entry.

Everything is our own description of a look (colours, finish, motif). No issuer artwork or logos.
Colours are approximations from memory of the real products.

Art shape (what the phone reads, under ``art`` / ``perks.art``)::

    {"stops": ["#RRGGBB", ...2-3], "finish": "matte|gloss|metal", "accent": "#RRGGBB", "text": "#RRGGBB",
     "network": "visa|mastercard|amex", "tier": "Platinum", "bank": "Emirates NBD",
     "chip": "gold|silver|dark", "contactless": true, "motif": "none|waves|lines|dots|arcs|grid"}
"""

from __future__ import annotations

import re
from typing import Any

FINISHES = {"matte", "gloss", "metal"}
CHIPS = {"gold", "silver", "dark"}
MOTIFS = {"none", "waves", "lines", "dots", "arcs", "grid"}
MARKS = {"visa", "mastercard", "amex"}
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
MIN_CONTRAST = 4.5


# ---------- colour maths ----------


def _rgb(h: str) -> tuple[float, float, float]:
    return int(h[1:3], 16) / 255, int(h[3:5], 16) / 255, int(h[5:7], 16) / 255


def _hex(c: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(v * 255))):02X}" for v in c)


def mix(a: str, b: str, t: float) -> str:
    ca, cb = _rgb(a), _rgb(b)
    return _hex(tuple(x + (y - x) * t for x, y in zip(ca, cb, strict=True)))  # type: ignore[arg-type]


def luminance(h: str) -> float:
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(v) for v in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ---------- bank palettes: (gradient start, gradient end, accent, display name) ----------

BANKS: dict[str, tuple[str, str, str, str]] = {
    "Emirates NBD": ("#0B2A5E", "#1B4C9C", "#FF9F43", "Emirates NBD"),
    "Emirates Islamic": ("#0A5A55", "#12857A", "#E2C275", "Emirates Islamic"),
    "First Abu Dhabi Bank": ("#0A1F44", "#0F4C7A", "#4FD1C5", "FAB"),
    "ADCB": ("#8E1233", "#C41E3A", "#FFB3C0", "ADCB"),
    "Mashreq": ("#1D1F24", "#4A2C1A", "#FF7A1A", "Mashreq"),
    "Dubai Islamic Bank": ("#064A33", "#0C6B48", "#D4B35E", "Dubai Islamic Bank"),
    "ADIB": ("#162766", "#2A3F9A", "#C9A45C", "ADIB"),
    "CBD": ("#0B4F52", "#13777A", "#7BD0C0", "CBD"),
    "RAKBANK": ("#8A0F1C", "#B8182A", "#FFC7CE", "RAKBANK"),
    "HSBC": ("#1B1B1F", "#4A1014", "#E0242B", "HSBC"),
    "Standard Chartered": ("#0A3A85", "#0B63B8", "#3CCB5A", "Standard Chartered"),
    "Citibank": ("#0F3F8C", "#1B6BCB", "#FF5A6E", "Citibank"),
    "Liv": ("#3A2BB5", "#7B3FD0", "#B8F2E6", "Liv"),
    "NBF": ("#0F2B4C", "#1D4A7A", "#D4B35E", "NBF"),
    "SIB": ("#0A5A3B", "#12804F", "#E2C275", "SIB"),
    "United Arab Bank": ("#1A3470", "#2A52A0", "#9CC3FF", "UAB"),
    "Ajman Bank": ("#7A2A14", "#B5391C", "#FFC9A8", "Ajman Bank"),
    "Al Masraf": ("#5E1430", "#8A1F44", "#F0B6C8", "Al Masraf"),
    "Commercial Bank International": ("#0F4250", "#17697C", "#8FDCE8", "CBI"),
    "Mawarid": ("#2F5A22", "#4A8034", "#D8E8A8", "Mawarid"),
    "Aafaq Islamic Finance": ("#40236E", "#5E37A0", "#D9C3FF", "Aafaq"),
    "Deem Finance": ("#26338A", "#3E52C4", "#C5CCFF", "Deem"),
    "Invest Bank": ("#1C2A40", "#2F4566", "#D4B35E", "Invest Bank"),
    "Al Hilal Bank": ("#0A5C41", "#128A5F", "#CDEBD9", "Al Hilal"),
    "American Express": ("#8A8F96", "#C9CDD2", "#2B2F36", "American Express"),
    "Dubai First": ("#111C36", "#22366A", "#7FB1FF", "Dubai First"),
    "Ruya Bank": ("#0A5E5E", "#118A86", "#BDF0E8", "Ruya"),
    "Tabby": ("#3EEDBF", "#8DF5D8", "#0B0B0B", "tabby"),
    "Wio": ("#0E0E10", "#25262B", "#C6F432", "Wio"),
    "National Bank of Umm Al Quwain": ("#14423A", "#1F6757", "#D4B35E", "NBQ"),
}
DEFAULT_PALETTE = ("#24303F", "#3B4A5E", "#9FB4CC", "")

# ---------- tier: finish, how far the face is pulled toward black, chip ----------

# tier -> (label, finish, darken, chip)
TIERS: dict[str, tuple[str, str, float, str]] = {
    "infinite": ("Infinite", "metal", 0.50, "silver"),
    "world_elite": ("World Elite", "metal", 0.38, "gold"),
    "signature": ("Signature", "gloss", 0.22, "gold"),
    "world": ("World", "gloss", 0.14, "gold"),
    "platinum": ("Platinum", "gloss", 0.0, "gold"),
    "titanium": ("Titanium", "metal", 0.0, "silver"),
    "gold": ("Gold", "gloss", 0.0, "gold"),
    "classic": ("Classic", "matte", 0.0, "gold"),
    "other": ("", "matte", 0.0, "gold"),
}
KIND_LABEL = {
    "cashback": "Cashback",
    "miles": "Miles",
    "points": "Rewards",
    "cobrand": "Credit Card",
    "lifestyle": "Lifestyle",
    "basic": "Classic",
}
KIND_MOTIF = {
    "cashback": "waves",
    "miles": "arcs",
    "points": "lines",
    "cobrand": "dots",
    "lifestyle": "grid",
    "basic": "none",
}
TITANIUM_GREY = ("#4A4E54", "#666B73")
NAME_TIERS = (
    ("world elite", "world_elite"),
    ("infinite", "infinite"),
    ("signature", "signature"),
    ("titanium", "titanium"),
    ("platinum", "platinum"),
    ("world", "world"),
    ("gold", "gold"),
    ("classic", "classic"),
)

# ---------- known products: partial overrides applied on top of the generated art ----------

_EK = {"stops": ["#8A0F1E", "#B01428", "#5E0A15"], "accent": "#E2C275"}  # Emirates red
_EK_BLACK = {
    "stops": ["#14151A", "#2A2D33", "#16181C"],
    "accent": "#C9A45C",
    "text": "#F1E6C8",
    "finish": "metal",
}
_ETIHAD = {"stops": ["#4A3A1C", "#7A5F2C", "#3A2D14"], "accent": "#E8C77A"}
_NOON = {
    "stops": ["#FFE62E", "#FFD500", "#F2C200"],
    "accent": "#111111",
    "text": "#111111",
    "finish": "matte",
    "chip": "dark",
}

OVERRIDES: dict[str, dict[str, Any]] = {
    # Emirates Skywards: red for Platinum/Signature, black + gold metal at the top.
    "enbd-skywards-infinite": {**_EK_BLACK, "motif": "lines"},
    "enbd-skywards-signature": {**_EK, "finish": "gloss", "motif": "waves"},
    "ei-skywards-black": {**_EK_BLACK, "motif": "arcs"},
    "ei-skywards-infinite": {**_EK_BLACK, "motif": "lines"},
    "ei-skywards-signature": {**_EK, "finish": "gloss", "motif": "waves"},
    "adib-skywards-infinite": {**_EK_BLACK, "motif": "lines"},
    "adib-skywards-signature": {**_EK, "finish": "gloss", "motif": "waves"},
    "dib-skywards-infinite": {**_EK_BLACK, "motif": "lines"},
    "dib-skywards-signature": {**_EK, "finish": "gloss", "motif": "waves"},
    "dib-skywards-platinum": {**_EK, "motif": "waves"},
    "hsbc-skywards-infinite": {**_EK_BLACK, "motif": "lines"},
    "hsbc-skywards-signature": {**_EK, "finish": "gloss", "motif": "waves"},
    # Etihad Guest: bronze and gold.
    "adib-etihad-guest-gold": {**_ETIHAD},
    "adib-etihad-guest-platinum": {**_ETIHAD},
    "adib-etihad-guest-infinite": {**_ETIHAD, "finish": "metal"},
    "ei-etihad-ameera": {**_ETIHAD},
    "ei-etihad-platinum": {**_ETIHAD},
    "ei-etihad-premium": {**_ETIHAD, "finish": "metal"},
    "ei-etihad-saqer": {**_ETIHAD},
    "enbd-etihad-guest-visa-elevate": {**_ETIHAD, "finish": "metal"},
    "enbd-etihad-guest-visa-inspire": {**_ETIHAD},
    "fab-etihad-guest-infinite": {**_ETIHAD, "finish": "metal"},
    "fab-etihad-guest-platinum": {**_ETIHAD},
    "fab-etihad-guest-signature": {**_ETIHAD},
    # noon: yellow with black type.
    "mashreq-noon": {**_NOON, "motif": "dots"},
    "enbd-noon-one": {**_NOON, "motif": "none"},
    # LuLu: green with a red accent.
    "adcb-lulu-platinum": {"stops": ["#0B5A2E", "#12783D", "#0A4424"], "accent": "#E23B3B"},
    "adcb-lulu-titanium": {
        "stops": ["#0B5A2E", "#12783D", "#0A4424"],
        "accent": "#E23B3B",
        "finish": "metal",
    },
    "enbd-lulu-247-platinum": {"stops": ["#0B5A2E", "#12783D", "#0A4424"], "accent": "#E23B3B"},
    "enbd-lulu-247-titanium": {
        "stops": ["#0B5A2E", "#12783D", "#0A4424"],
        "accent": "#E23B3B",
        "finish": "metal",
    },
    # Amazon: dark navy with orange.
    "ei-amazon-platinum": {"stops": ["#131A22", "#232F3E", "#131A22"], "accent": "#FF9900"},
    "ei-amazon-world": {"stops": ["#131A22", "#232F3E", "#131A22"], "accent": "#FF9900", "finish": "metal"},
    # Marriott Bonvoy: deep burgundy.
    "adib-marriott-bonvoy-infinite": {"stops": ["#4A1424", "#7A2338", "#3A0F1C"], "accent": "#E8A87C"},
    "adib-marriott-bonvoy-signature": {"stops": ["#4A1424", "#7A2338", "#3A0F1C"], "accent": "#E8A87C"},
    "enbd-marriott-bonvoy-world": {"stops": ["#4A1424", "#7A2338", "#3A0F1C"], "accent": "#E8A87C"},
    "enbd-marriott-bonvoy-world-elite": {
        "stops": ["#4A1424", "#7A2338", "#3A0F1C"],
        "accent": "#E8A87C",
        "finish": "metal",
    },
    # Barca: blue and garnet.
    "ei-barca-cashback": {"stops": ["#0A3F86", "#3A2A7A", "#8E1440"], "accent": "#FFD24A", "finish": "gloss"},
    # Talabat: orange.
    "adcb-talabat": {"stops": ["#B53A00", "#E05200", "#8A2C00"], "accent": "#FFE0CC"},
    # Black cards.
    "mashreq-solitaire": {
        "stops": ["#0B0C0E", "#23262B", "#0E0F12"],
        "accent": "#D7DBE0",
        "text": "#E9ECEF",
        "chip": "silver",
        "motif": "lines",
        "finish": "metal",
    },
    "citi-prestige": {
        "stops": ["#0E0E10", "#26272B", "#101012"],
        "accent": "#D7DBE0",
        "finish": "metal",
        "chip": "silver",
    },
    "citi-ultima": {
        "stops": ["#0E0E10", "#26272B", "#101012"],
        "accent": "#D7DBE0",
        "finish": "metal",
        "chip": "silver",
    },
    "hsbc-premier": {
        "stops": ["#101012", "#2A2A2F", "#5A0D12"],
        "accent": "#E0242B",
        "finish": "metal",
        "chip": "silver",
    },
    "enbd-priority-banking-visa-infinite": {
        "stops": ["#0B1530", "#1B2D5E", "#0B1530"],
        "accent": "#C9A45C",
        "finish": "metal",
    },
    # Amex Platinum: brushed silver with dark type.
    "amex-platinum-uae": {
        "stops": ["#A9AEB5", "#D2D5D9", "#9EA3AA"],
        "accent": "#2B2F36",
        "text": "#16181C",
        "finish": "metal",
        "chip": "silver",
        "network": "amex",
        "motif": "none",
    },
    # Tabby: mint with black type.
    "tabby-card": {"text": "#0B0B0B", "finish": "matte", "chip": "dark", "motif": "none"},
    # Wio: black with lime.
    "wio-credit": {"finish": "matte", "chip": "silver", "motif": "none"},
    # Diners Club: Diners blue.
    "enbd-diners-club": {
        "stops": ["#0A4A7A", "#1280B8", "#0A3A62"],
        "accent": "#BFE3F7",
        "network": "mastercard",
    },
    "enbd-duo": {"stops": ["#0A4A7A", "#1280B8", "#0A3A62"], "accent": "#BFE3F7"},
    # Titanium cards keep a brushed grey body with the bank's accent.
    "rakbank-titanium": {"accent": "#E53935"},
    # Co-brands.
    "mashreq-aura": {"stops": ["#14213D", "#2D2A55", "#14213D"], "accent": "#9D8CFF", "motif": "arcs"},
    "mashreq-aura-world": {"stops": ["#14213D", "#2D2A55", "#14213D"], "accent": "#9D8CFF", "motif": "arcs"},
    "mashreq-aura-world-elite": {
        "stops": ["#14213D", "#2D2A55", "#14213D"],
        "accent": "#9D8CFF",
        "motif": "arcs",
    },
    "fab-adnoc-rewards": {"stops": ["#0B2E6B", "#164A9E", "#8A1020"], "accent": "#FF5A5A"},
    "fab-gems-world": {"stops": ["#0A5C66", "#128A96", "#0A4650"], "accent": "#E2C275"},
    "adcb-shukran": {"stops": ["#1B1D22", "#2E2A28", "#7A3A12"], "accent": "#FF8A3D"},
    "mashreq-cashback": {"stops": ["#1B1D22", "#2D2A2A", "#8A3A0E"], "accent": "#FF8A3D"},
    "fab-cashback": {"stops": ["#0A1F3F", "#0F3B5C", "#0B5560"], "accent": "#5FD4C4"},
}


# ---------- generation ----------


def infer_tier(card: dict[str, Any]) -> str:
    t = card.get("tier") or "other"
    if t != "other":
        return t
    name = card.get("name", "").lower()
    for needle, tier in NAME_TIERS:
        if needle in name:
            return tier
    return "other"


def guess_network(card: dict[str, Any], tier: str) -> str:
    n = card.get("network")
    if n in MARKS:
        return str(n)
    return "mastercard" if tier in {"world", "world_elite", "titanium"} else "visa"


def _readable(stops: list[str], text: str) -> list[str]:
    """Darken (white text) or lighten (dark text) the stops until ``text`` clears the contrast floor."""
    toward = "#000000" if luminance(text) > 0.5 else "#FFFFFF"
    for _ in range(40):
        if all(contrast(text, s) >= MIN_CONTRAST for s in stops):
            break
        stops = [mix(s, toward, 0.05) for s in stops]
    return stops


def generate(card: dict[str, Any]) -> dict[str, Any]:
    issuer = card["issuer"]
    c1, c2, accent, bank = BANKS.get(issuer, DEFAULT_PALETTE)
    bank = bank or issuer
    tier = infer_tier(card)
    label, finish, dark, chip = TIERS[tier]
    if not label:
        label = KIND_LABEL.get(card.get("kind", ""), "Credit Card")
    if tier == "titanium":
        c1, c2 = TITANIUM_GREY
    stops = [c1, c2, mix(c1, "#000000", 0.35)]
    if dark:
        stops = [mix(s, "#000000", dark) for s in stops]
    text = "#FFFFFF"
    art: dict[str, Any] = {
        "stops": stops,
        "finish": finish,
        "accent": accent,
        "text": text,
        "network": guess_network(card, tier),
        "tier": label,
        "bank": bank,
        "chip": chip,
        "contactless": True,
        "motif": "grid"
        if card.get("islamic") and card.get("kind") not in {"miles"}
        else KIND_MOTIF.get(card.get("kind", ""), "none"),
    }
    art.update(OVERRIDES.get(card["id"], {}))
    if issuer == "Tabby" or card["id"] in {"tabby-card"}:
        art["text"] = "#0B0B0B"
    art["stops"] = _readable(list(art["stops"]), art["text"])
    return art


# ---------- validation ----------


def validate_art(art: Any, card: dict[str, Any]) -> list[str]:
    cid = card.get("id", "?")
    errs: list[str] = []
    if not isinstance(art, dict):
        return [f"{cid}: art missing"]
    stops = art.get("stops")
    if not (isinstance(stops, list) and 2 <= len(stops) <= 3 and all(HEX.match(str(s)) for s in stops)):
        errs.append(f"{cid}: art.stops must be 2-3 #RRGGBB")
        return errs
    for k in ("accent", "text"):
        if not HEX.match(str(art.get(k, ""))):
            errs.append(f"{cid}: art.{k} must be #RRGGBB")
    if errs:
        return errs
    if art.get("finish") not in FINISHES:
        errs.append(f"{cid}: bad art.finish")
    if art.get("chip") not in CHIPS:
        errs.append(f"{cid}: bad art.chip")
    if art.get("motif") not in MOTIFS:
        errs.append(f"{cid}: bad art.motif")
    if art.get("network") not in MARKS:
        errs.append(f"{cid}: bad art.network")
    elif card.get("network") in MARKS and card["network"] != art["network"]:
        errs.append(f"{cid}: art.network {art['network']} != card network {card['network']}")
    if not art.get("bank"):
        errs.append(f"{cid}: art.bank missing")
    if not art.get("tier"):
        errs.append(f"{cid}: art.tier missing")
    for s in stops:
        if contrast(art["text"], s) < MIN_CONTRAST:
            errs.append(
                f"{cid}: text {art['text']} on {s} is {contrast(art['text'], s):.2f}:1 (< {MIN_CONTRAST})"
            )
    return errs
