"""Merchant normalisation and the curated UAE merchant dictionary.

Everything here is deterministic and offline: raw card/SMS descriptors such as
"CARREFOUR MOE DUBAI AE 0123" are cleaned and matched against ``DICTIONARY`` (first match wins,
so put specific entries before generic ones). Unknown merchants fall back to a cleaned,
title-cased descriptor. To teach the app a new merchant add one ``_e(...)`` line.
"""

import re
from dataclasses import dataclass

from app.models.transaction import Direction

_WS = re.compile(r"\s+")
_LEADING_NOISE = re.compile(
    r"^(?:(?:POS|PUR|PURCHASE|ECOM|E-COM|ONLINE|CARD|DEBIT|VISA|MASTERCARD|MC|RECURRING|CONTACTLESS|NFC)"
    r"(?:\s+(?:PURCHASE|PAYMENT|TXN|TRANSACTION))?[\s:*\-]+)+"
)
# Payment-gateway prefixes that hide the real merchant ("PAYFORT*NOON" -> "NOON").
_GATEWAY_PREFIX = re.compile(
    r"^(?:PAYFORT|PAYPAL|SQ|SQU|TST|CKO|CHECKOUT\.COM|STRIPE|PAY\.GOV|WWW|HTTPS?://)[\s*:\-/]+"
)
_CITIES = (
    r"DUBAI|ABU\s?DHABI|SHARJAH|AJMAN|RAS\s?AL\s?KHAIMAH|RAK|FUJAIRAH|AL\s?AIN|UMM\s?AL\s?QUWAIN|UAQ|"
    r"DXB|AUH|SHJ|LONDON|NEW\s?YORK|SINGAPORE|MUMBAI|DOHA|RIYADH"
)
_COUNTRIES = r"AE|ARE|UAE|UNITED\s?ARAB\s?EMIRATES|US|USA|GB|GBR|UK|IN|IND|SG|SGP|QA|SA"
_TRAILING_LOCATION = re.compile(rf"(?:[\s,\-]+(?:{_CITIES}|{_COUNTRIES}))+$")
_TRAILING_REF = re.compile(r"[\s#*\-]*\d{3,}$")
_TRAILING_CCY = re.compile(r"\s+(?:AED|USD|EUR|GBP)\s*[\d.,]*$")


@dataclass(frozen=True, slots=True)
class MerchantEntry:
    name: str
    category: str  # name of a system category (see migration 0001)
    pattern: re.Pattern[str]
    direction: Direction | None = None  # restrict to debits/credits when set


def _e(name: str, category: str, *alts: str, direction: Direction | None = None) -> MerchantEntry:
    body = "|".join(alts)
    return MerchantEntry(name, category, re.compile(rf"(?<![A-Z0-9])(?:{body})(?![A-Z0-9])"), direction)


_CR, _DR = Direction.credit, Direction.debit

DICTIONARY: tuple[MerchantEntry, ...] = (
    # --- bank-originated lines (before everything else) ---
    _e("Salary", "Salary", r"SALARY", r"WPS", r"PAYROLL", direction=_CR),
    _e(
        "Card Payment",
        "Credit Card Payment",
        r"PAYMENT RECEIVED",
        r"PAYMENT\s*-?\s*THANK\s*YOU",
        r"THANK YOU FOR YOUR PAYMENT",
        r"AUTOPAY",
        r"CARD PAYMENT",
        direction=_CR,
    ),
    _e(
        "Bank Fees",
        "Fees & Charges",
        r"ANNUAL FEE",
        r"LATE PAYMENT FEE",
        r"FINANCE CHARGE",
        r"INTEREST CHARGE",
        r"INTEREST",
        r"SMS (?:ALERT )?FEE",
        r"SERVICE CHARGE",
        r"CASH ADVANCE FEE",
        r"OVER ?LIMIT FEE",
        r"MONTHLY FEE",
        r"VAT(?: ON FEES?)?",
        r"FOREIGN (?:CURRENCY|TXN|TRANSACTION) (?:FEE|MARKUP)",
        r"CARD FEE",
        direction=_DR,
    ),
    _e("ATM Withdrawal", "Other", r"ATM(?: WITHDRAWAL| CASH)?", r"CASH WITHDRAWAL", r"CASH ADVANCE"),
    _e(
        "Transfer",
        "Transfer",
        r"(?:IPI|IBAN|FUND|ONLINE|MOBILE)? ?TRANSFER",
        r"TRF",
        r"SEND MONEY",
        r"TRANSFERWISE",
        r"WISE PAYMENTS?",
        r"LULU EXCHANGE",
        r"AL ANSARI EXCHANGE",
        r"ALANSARI",
        r"REMITLY",
    ),
    # --- transport (Careem Food/Uber Eats are dining, so they come first) ---
    _e("Careem Food", "Dining", r"CAREEM\s*FOOD", r"CAREEM\s*NOW"),
    _e("Uber Eats", "Dining", r"UBER\s*\*?\s*EATS", r"UBEREATS"),
    _e("Careem", "Transport", r"CAREEM(?:\s*QUIK)?", r"CAREEM\s*RIDES?"),
    _e("Uber", "Transport", r"UBER(?:\s*\*?\s*(?:TRIP|RIDE))?"),
    _e("Salik", "Transport", r"SALIK", r"DARB"),
    _e(
        "RTA",
        "Transport",
        r"RTA",
        r"NOL(?:\s*CARD|\s*TOP\s*UP)?",
        r"MAWAQIF",
        r"PARKIN",
        r"DUBAI TAXI",
        r"ROAD(?:S)? (?:AND|&) TRANSPORT",
    ),
    # --- fuel ---
    _e("ENOC", "Fuel", r"ENOC", r"EPPCO"),
    _e("ADNOC", "Fuel", r"ADNOC(?:\s*(?:DISTRIBUTION|FUEL|STATION|OASIS))?"),
    _e("Emarat", "Fuel", r"EMARAT(?:\s*PETROL)?"),
    # --- utilities & telecom ---
    _e("DEWA", "Utilities", r"DEWA", r"DUBAI ELECTRICITY(?: (?:AND|&) WATER)?(?: AUTHORITY)?"),
    _e("SEWA", "Utilities", r"SEWA", r"SHARJAH ELECTRICITY"),
    _e("FEWA", "Utilities", r"FEWA"),
    _e("AADC", "Utilities", r"AADC", r"ADDC", r"ABU DHABI DISTRIBUTION"),
    _e("Empower", "Utilities", r"EMPOWER(?: DISTRICT COOLING)?", r"TABREED"),
    _e("e&", "Utilities", r"ETISALAT", r"E&", r"EAND", r"E AND", r"EMIRATES TELECOM(?:MUNICATIONS?)?"),
    _e("du", "Utilities", r"DU", r"EMIRATES INTEGRATED TELE(?:COM(?:MUNICATIONS?)?)?"),
    _e("Virgin Mobile", "Utilities", r"VIRGIN MOBILE"),
    _e("Dubai Municipality", "Utilities", r"DUBAI MUNICIPALITY"),
    # --- BNPL ---
    _e("Tabby", "Shopping", r"TABBY"),
    _e("Tamara", "Shopping", r"TAMARA"),
    # --- groceries ---
    _e("Carrefour", "Groceries", r"(?:MAF )?CARREFOUR(?: (?:HYPER(?:MARKET)?|MARKET|EXPRESS))?", r"CRF"),
    _e("Lulu", "Groceries", r"LULU(?: (?:HYPER(?:MARKET)?|EXPRESS|CENTER|CENTRE|MARKET))?"),
    _e("Spinneys", "Groceries", r"SPINNEYS?"),
    _e("Waitrose", "Groceries", r"WAITROSE"),
    _e("Choithrams", "Groceries", r"CHOITHRAMS?"),
    _e("Union Coop", "Groceries", r"UNION CO-?OP(?:ERATIVE)?", r"UNION COOP"),
    _e("Al Maya", "Groceries", r"AL MAYA"),
    _e("Viva", "Groceries", r"VIVA (?:SUPERMARKET|SUPER MARKET)"),
    _e("Kibsons", "Groceries", r"KIBSONS"),
    _e("InstaShop", "Groceries", r"INSTASHOP"),
    _e("Grandiose", "Groceries", r"GRANDIOSE"),
    _e("Zoom", "Groceries", r"ZOOM(?: STORE| CONVENIENCE)?"),
    # --- dining ---
    _e("Talabat", "Dining", r"TALABAT(?:\s*(?:MART|FOOD))?"),
    _e("Deliveroo", "Dining", r"DELIVEROO"),
    _e("Noon Food", "Dining", r"NOON\s*FOOD", r"NOONFOOD"),
    _e("Zomato", "Dining", r"ZOMATO"),
    _e("Starbucks", "Dining", r"STARBUCKS"),
    _e("McDonald's", "Dining", r"MC ?DONALD'?S?"),
    _e("KFC", "Dining", r"KFC", r"KENTUCKY FRIED"),
    _e("Burger King", "Dining", r"BURGER KING"),
    _e("Costa Coffee", "Dining", r"COSTA(?: COFFEE)?"),
    _e("Tim Hortons", "Dining", r"TIM HORTONS?"),
    _e("Dunkin'", "Dining", r"DUNKIN(?:'| DONUTS)?"),
    _e("Pizza Hut", "Dining", r"PIZZA HUT"),
    _e("Domino's", "Dining", r"DOMINO'?S"),
    _e("Shake Shack", "Dining", r"SHAKE SHACK"),
    _e("Subway", "Dining", r"SUBWAY"),
    # --- subscriptions ---
    _e("Netflix", "Subscriptions", r"NETFLIX"),
    _e("Spotify", "Subscriptions", r"SPOTIFY"),
    _e(
        "Apple",
        "Subscriptions",
        r"APPLE\.COM(?:/BILL)?",
        r"ITUNES",
        r"APPLE (?:SERVICES|MUSIC|ICLOUD)",
        r"ICLOUD",
    ),
    _e(
        "Google",
        "Subscriptions",
        r"GOOGLE\s*\*?\s*(?:PLAY|YOUTUBE|ONE|STORAGE|WORKSPACE)?",
        r"YOUTUBE(?: PREMIUM)?",
    ),
    _e("Shahid", "Subscriptions", r"SHAHID(?: VIP)?"),
    _e("OSN+", "Subscriptions", r"OSN\+?"),
    _e("Anghami", "Subscriptions", r"ANGHAMI"),
    _e("Disney+", "Subscriptions", r"DISNEY\s*\+?(?:\s*PLUS)?"),
    _e("Amazon Prime", "Subscriptions", r"AMAZON\s*PRIME", r"PRIME VIDEO"),
    _e("OpenAI", "Subscriptions", r"OPENAI", r"CHATGPT"),
    _e("Microsoft", "Subscriptions", r"MICROSOFT(?: \*?\s*(?:365|365 PERSONAL|XBOX))?", r"MSFT"),
    _e("Adobe", "Subscriptions", r"ADOBE"),
    # --- shopping ---
    _e("Noon", "Shopping", r"NOON(?:\.COM)?(?: MINUTES)?"),
    _e(
        "Amazon.ae",
        "Shopping",
        r"AMAZON\.AE",
        r"AMAZON\s*AE",
        r"AMZN(?: MKTP)?(?: AE)?",
        r"AMAZON(?:\.COM)?(?: (?:MKTPLACE|MARKETPLACE))?",
        r"AMAZON",
    ),
    _e("Namshi", "Shopping", r"NAMSHI"),
    _e("IKEA", "Shopping", r"IKEA"),
    _e("Home Centre", "Shopping", r"HOME CENTRE", r"HOME CENTER"),
    _e("Sharaf DG", "Shopping", r"SHARAF ?DG", r"SHARAF DG"),
    _e("Jumbo Electronics", "Shopping", r"JUMBO ELECTRONICS?"),
    _e("H&M", "Shopping", r"H ?& ?M", r"H AND M"),
    _e("Zara", "Shopping", r"ZARA"),
    _e("Max Fashion", "Shopping", r"MAX FASHION", r"MAX STORES?"),
    _e("Centrepoint", "Shopping", r"CENTRE ?POINT", r"CENTER ?POINT"),
    _e("Ounass", "Shopping", r"OUNASS"),
    _e("Shein", "Shopping", r"SHEIN"),
    _e("AliExpress", "Shopping", r"ALIEXPRESS", r"ALIPAY"),
    # --- health ---
    _e("Aster", "Health", r"ASTER(?: PHARMACY| CLINIC| HOSPITAL)?"),
    _e("Life Pharmacy", "Health", r"LIFE PHARMACY"),
    _e("Boots", "Health", r"BOOTS(?: PHARMACY)?"),
    _e("Mediclinic", "Health", r"MEDICLINIC"),
    _e("NMC Healthcare", "Health", r"NMC(?: HEALTHCARE| HOSPITAL)?"),
    _e("Medcare", "Health", r"MEDCARE"),
    # --- travel / leisure ---
    _e("Emirates Airline", "Travel", r"EMIRATES (?:AIRLINES?|AIR|SKYWARDS)"),
    _e("Etihad Airways", "Travel", r"ETIHAD(?: AIRWAYS)?"),
    _e("flydubai", "Travel", r"FLY ?DUBAI"),
    _e("Air Arabia", "Travel", r"AIR ARABIA"),
    _e("Booking.com", "Travel", r"BOOKING\.COM", r"BOOKING COM"),
    _e("Airbnb", "Travel", r"AIRBNB"),
    _e("Agoda", "Travel", r"AGODA"),
    _e("Expedia", "Travel", r"EXPEDIA"),
    _e("VOX Cinemas", "Entertainment", r"VOX(?: CINEMAS?)?"),
    _e("Reel Cinemas", "Entertainment", r"REEL(?: CINEMAS?)?"),
    _e("Platinumlist", "Entertainment", r"PLATINUM ?LIST"),
    _e("Dubai Parks", "Entertainment", r"DUBAI PARKS"),
    # --- housing / education ---
    _e("Ejari", "Rent", r"EJARI", r"DUBAI LAND (?:DEPARTMENT|DEPT)", r"RERA"),
    _e("GEMS Education", "Education", r"GEMS(?: EDUCATION)?", r"KHDA"),
)


def clean_descriptor(raw: str) -> str:
    """Upper-case the descriptor and strip POS/gateway prefixes, trailing city/country and ref numbers."""
    s = _WS.sub(" ", raw.upper().replace(" ", " ")).strip()
    for _ in range(2):  # prefixes can stack: "POS PURCHASE PAYFORT*NOON"
        s = _LEADING_NOISE.sub("", s)
        s = _GATEWAY_PREFIX.sub("", s)
    prev = None
    while prev != s:  # location, country, currency and reference suffixes can interleave
        prev = s
        s = _TRAILING_LOCATION.sub("", s)
        s = _TRAILING_CCY.sub("", s)
        s = _TRAILING_REF.sub("", s)
        s = s.strip(" -*#,.")
    return s


def match_entry(raw: str | None, direction: Direction | None = None) -> MerchantEntry | None:
    if not raw:
        return None
    cleaned = clean_descriptor(raw)
    full = _WS.sub(" ", raw.upper())
    for entry in DICTIONARY:
        if entry.direction is not None and direction is not None and entry.direction is not direction:
            continue
        if entry.pattern.search(cleaned) or entry.pattern.search(full):
            return entry
    return None


def normalize_merchant(raw: str | None, direction: Direction | None = None) -> str | None:
    """Canonical merchant name: dictionary hit, else the cleaned descriptor in title case."""
    if not raw or not raw.strip():
        return None
    entry = match_entry(raw, direction)
    if entry:
        return entry.name
    cleaned = clean_descriptor(raw)
    return cleaned.title()[:160] or None


def guess_category_name(raw: str | None, direction: Direction | None = None) -> str | None:
    entry = match_entry(raw, direction)
    return entry.category if entry else None


def merchant_key(normalized: str | None) -> str | None:
    """Rule key: case/whitespace-insensitive normalised merchant."""
    if not normalized or not normalized.strip():
        return None
    return _WS.sub(" ", normalized.strip().lower())[:160]
