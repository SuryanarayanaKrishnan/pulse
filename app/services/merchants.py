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
    category: str  # slug of a default category (see migration 0003)
    pattern: re.Pattern[str]
    direction: Direction | None = None  # restrict to debits/credits when set


def _e(name: str, category: str, *alts: str, direction: Direction | None = None) -> MerchantEntry:
    body = "|".join(alts)
    return MerchantEntry(name, category, re.compile(rf"(?<![A-Z0-9])(?:{body})(?![A-Z0-9])"), direction)


_CR, _DR = Direction.credit, Direction.debit

DICTIONARY: tuple[MerchantEntry, ...] = (
    # --- bank-originated lines (before everything else) ---
    _e("Salary", "salary", r"SALARY", r"WPS", r"PAYROLL", direction=_CR),
    _e(
        "Card Payment",
        "cc_payment",
        r"PAYMENT RECEIVED",
        r"PAYMENT\s*-?\s*THANK\s*YOU",
        r"THANK YOU FOR YOUR PAYMENT",
        r"AUTOPAY",
        r"CARD PAYMENT",
        direction=_CR,
    ),
    _e("Credit Card Payment", "cc_payment", r"CREDIT CARD PAYMENT", r"CC PAYMENT", direction=_DR),
    _e(
        "Interest Income",
        "other_income",
        r"INTEREST(?: PAID| CREDIT(?:ED)?| EARNED)?",
        r"PROFIT PAID",
        direction=_CR,
    ),
    _e("Inward Remittance", "other_income", r"INWARD REMITTANCE", r"INCOMING REMITTANCE", direction=_CR),
    _e(
        "Wallet Top-up",
        "transfer",
        r"WALLET (?:TOP ?-?UP|RELOAD|LOAD)",
        r"TOP ?-?UP (?:TO )?WALLET",
        direction=_DR,
    ),
    _e(
        "Bank Fees",
        "fees",
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
    _e(
        "ATM Withdrawal",
        "other",
        r"ATM(?: WITHDRAWAL| CASH)?",
        r"CASH WITHDRAWAL",
        r"CASH ADVANCE",
        r"CARDLESS CASH",
    ),
    _e(
        "Remittance",
        "family",
        r"TRANSFERWISE",
        r"WISE PAYMENTS?",
        r"LULU EXCHANGE",
        r"AL ANSARI EXCHANGE",
        r"ALANSARI",
        r"REMITLY",
        r"WESTERN UNION",
        r"UAE EXCHANGE",
        r"AL FARDAN EXCHANGE",
        r"QUICK ?REMIT",
        r"REMIT(?:TANCE)?",
        direction=_DR,
    ),
    _e("Car Loan", "car_loan", r"CAR LOAN", r"AUTO LOAN", r"VEHICLE (?:LOAN|FINANCE)", direction=_DR),
    _e(
        "Home Loan",
        "home_loan",
        r"HOME LOAN",
        r"MORTGAGE",
        r"HOUSING LOAN",
        r"HOME FINANCE",
        direction=_DR,
    ),
    _e(
        "Insurance",
        "fixed_expenses",
        r"INSURANCE",
        r"ASSURANCE",
        r"TAKAFUL",
        r"DAMAN",
        r"ORIENT INS(?:URANCE)?",
        direction=_DR,
    ),
    _e(
        "Transfer",
        "transfer",
        r"(?:IPI|IBAN|FUND|ONLINE|MOBILE)? ?TRANSFER",
        r"TRF",
        r"SEND MONEY",
    ),
    # --- transport (Careem Food/Uber Eats are dining, so they come first) ---
    _e("Careem Food", "dining", r"CAREEM\s*FOOD", r"CAREEM\s*NOW"),
    _e("Uber Eats", "dining", r"UBER\s*\*?\s*EATS", r"UBEREATS"),
    _e("Careem", "transport", r"CAREEM(?:\s*QUIK)?", r"CAREEM\s*RIDES?"),
    _e("Uber", "transport", r"UBER(?:\s*\*?\s*(?:TRIP|RIDE))?"),
    _e("Salik", "transport", r"SALIK", r"DARB"),
    _e(
        "RTA",
        "transport",
        r"RTA",
        r"NOL(?:\s*CARD|\s*TOP\s*UP)?",
        r"MAWAQIF",
        r"PARKIN",
        r"DUBAI TAXI",
        r"ROAD(?:S)? (?:AND|&) TRANSPORT",
    ),
    # --- fuel ---
    _e("ENOC", "transport", r"ENOC", r"EPPCO"),
    _e("ADNOC", "transport", r"ADNOC(?:\s*(?:DISTRIBUTION|FUEL|STATION|OASIS))?"),
    _e("Emarat", "transport", r"EMARAT(?:\s*PETROL)?"),
    # --- utilities & telecom ---
    _e("DEWA", "utilities", r"DEWA", r"DUBAI ELECTRICITY(?: (?:AND|&) WATER)?(?: AUTHORITY)?"),
    _e("SEWA", "utilities", r"SEWA", r"SHARJAH ELECTRICITY"),
    _e("FEWA", "utilities", r"FEWA"),
    _e("AADC", "utilities", r"AADC", r"ADDC", r"ABU DHABI DISTRIBUTION"),
    _e("Empower", "utilities", r"EMPOWER(?: DISTRICT COOLING)?", r"TABREED"),
    _e("e&", "telecom", r"ETISALAT", r"E&", r"EAND", r"E AND", r"EMIRATES TELECOM(?:MUNICATIONS?)?"),
    _e("du", "telecom", r"DU", r"EMIRATES INTEGRATED TELE(?:COM(?:MUNICATIONS?)?)?"),
    _e("Virgin Mobile", "telecom", r"VIRGIN MOBILE"),
    _e("Dubai Municipality", "utilities", r"DUBAI MUNICIPALITY"),
    # --- BNPL ---
    _e("Tabby", "shopping", r"TABBY"),
    _e("Tamara", "shopping", r"TAMARA"),
    # --- groceries ---
    _e("Carrefour", "groceries", r"(?:MAF )?CARREFOUR(?: (?:HYPER(?:MARKET)?|MARKET|EXPRESS))?", r"CRF"),
    _e("Lulu", "groceries", r"LULU(?: (?:HYPER(?:MARKET)?|EXPRESS|CENTER|CENTRE|MARKET))?"),
    _e("Spinneys", "groceries", r"SPINNEYS?"),
    _e("Waitrose", "groceries", r"WAITROSE"),
    _e("Choithrams", "groceries", r"CHOITHRAMS?"),
    _e("Union Coop", "groceries", r"UNION CO-?OP(?:ERATIVE)?", r"UNION COOP"),
    _e("Al Maya", "groceries", r"AL MAYA"),
    _e("Viva", "groceries", r"VIVA (?:SUPERMARKET|SUPER MARKET)"),
    _e("Kibsons", "groceries", r"KIBSONS"),
    _e("InstaShop", "groceries", r"INSTASHOP"),
    _e("Grandiose", "groceries", r"GRANDIOSE"),
    _e("Zoom", "groceries", r"ZOOM(?: STORE| CONVENIENCE)?"),
    # --- dining ---
    _e("Talabat", "dining", r"TALABAT(?:\s*(?:MART|FOOD))?"),
    _e("Deliveroo", "dining", r"DELIVEROO"),
    _e("Noon Food", "dining", r"NOON\s*FOOD", r"NOONFOOD"),
    _e("Zomato", "dining", r"ZOMATO"),
    _e("Starbucks", "dining", r"STARBUCKS"),
    _e("McDonald's", "dining", r"MC ?DONALD'?S?"),
    _e("KFC", "dining", r"KFC", r"KENTUCKY FRIED"),
    _e("Burger King", "dining", r"BURGER KING"),
    _e("Costa Coffee", "dining", r"COSTA(?: COFFEE)?"),
    _e("Tim Hortons", "dining", r"TIM HORTONS?"),
    _e("Dunkin'", "dining", r"DUNKIN(?:'| DONUTS)?"),
    _e("Pizza Hut", "dining", r"PIZZA HUT"),
    _e("Domino's", "dining", r"DOMINO'?S"),
    _e("Shake Shack", "dining", r"SHAKE SHACK"),
    _e("Subway", "dining", r"SUBWAY"),
    # --- subscriptions ---
    _e("Netflix", "fixed_expenses", r"NETFLIX"),
    _e("Spotify", "fixed_expenses", r"SPOTIFY"),
    _e(
        "Apple",
        "fixed_expenses",
        r"APPLE\.COM(?:/BILL)?",
        r"ITUNES",
        r"APPLE (?:SERVICES|MUSIC|ICLOUD)",
        r"ICLOUD",
    ),
    _e(
        "Google",
        "fixed_expenses",
        r"GOOGLE\s*\*?\s*(?:PLAY|YOUTUBE|ONE|STORAGE|WORKSPACE)?",
        r"YOUTUBE(?: PREMIUM)?",
    ),
    _e("Shahid", "fixed_expenses", r"SHAHID(?: VIP)?"),
    _e("OSN+", "fixed_expenses", r"OSN\+?"),
    _e("Anghami", "fixed_expenses", r"ANGHAMI"),
    _e("Disney+", "fixed_expenses", r"DISNEY\s*\+?(?:\s*PLUS)?"),
    _e("Amazon Prime", "fixed_expenses", r"AMAZON\s*PRIME", r"PRIME VIDEO"),
    _e("OpenAI", "fixed_expenses", r"OPENAI", r"CHATGPT"),
    _e("Microsoft", "fixed_expenses", r"MICROSOFT(?: \*?\s*(?:365|365 PERSONAL|XBOX))?", r"MSFT"),
    _e("Adobe", "fixed_expenses", r"ADOBE"),
    # --- shopping ---
    _e("Noon", "shopping", r"NOON(?:\.COM)?(?: MINUTES)?"),
    _e(
        "Amazon.ae",
        "shopping",
        r"AMAZON\.AE",
        r"AMAZON\s*AE",
        r"AMZN(?: MKTP)?(?: AE)?",
        r"AMAZON(?:\.COM)?(?: (?:MKTPLACE|MARKETPLACE))?",
        r"AMAZON",
    ),
    _e("Namshi", "shopping", r"NAMSHI"),
    _e("IKEA", "shopping", r"IKEA"),
    _e("Home Centre", "shopping", r"HOME CENTRE", r"HOME CENTER"),
    _e("Sharaf DG", "shopping", r"SHARAF ?DG", r"SHARAF DG"),
    _e("Jumbo Electronics", "shopping", r"JUMBO ELECTRONICS?"),
    _e("H&M", "shopping", r"H ?& ?M", r"H AND M"),
    _e("Zara", "shopping", r"ZARA"),
    _e("Max Fashion", "shopping", r"MAX FASHION", r"MAX STORES?"),
    _e("Centrepoint", "shopping", r"CENTRE ?POINT", r"CENTER ?POINT"),
    _e("Ounass", "shopping", r"OUNASS"),
    _e("Shein", "shopping", r"SHEIN"),
    _e("AliExpress", "shopping", r"ALIEXPRESS", r"ALIPAY"),
    # --- health ---
    _e("Aster", "health", r"ASTER(?: PHARMACY| CLINIC| HOSPITAL)?"),
    _e("Life Pharmacy", "health", r"LIFE PHARMACY"),
    _e("Boots", "health", r"BOOTS(?: PHARMACY)?"),
    _e("Mediclinic", "health", r"MEDICLINIC"),
    _e("NMC Healthcare", "health", r"NMC(?: HEALTHCARE| HOSPITAL)?"),
    _e("Medcare", "health", r"MEDCARE"),
    # --- travel / leisure ---
    _e("Emirates Airline", "travel", r"EMIRATES (?:AIRLINES?|AIR|SKYWARDS)"),
    _e("Etihad Airways", "travel", r"ETIHAD(?: AIRWAYS)?"),
    _e("flydubai", "travel", r"FLY ?DUBAI"),
    _e("Air Arabia", "travel", r"AIR ARABIA"),
    _e("Booking.com", "travel", r"BOOKING\.COM", r"BOOKING COM"),
    _e("Airbnb", "travel", r"AIRBNB"),
    _e("Agoda", "travel", r"AGODA"),
    _e("Expedia", "travel", r"EXPEDIA"),
    _e("VOX Cinemas", "entertainment", r"VOX(?: CINEMAS?)?"),
    _e("Reel Cinemas", "entertainment", r"REEL(?: CINEMAS?)?"),
    _e("Platinumlist", "entertainment", r"PLATINUM ?LIST"),
    _e("Dubai Parks", "entertainment", r"DUBAI PARKS"),
    # --- housing / education ---
    _e("Ejari", "rent", r"EJARI", r"DUBAI LAND (?:DEPARTMENT|DEPT)", r"RERA"),
    _e("GEMS Education", "education", r"GEMS(?: EDUCATION)?", r"KHDA"),
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


def guess_category_slug(raw: str | None, direction: Direction | None = None) -> str | None:
    entry = match_entry(raw, direction)
    return entry.category if entry else None


def merchant_key(normalized: str | None) -> str | None:
    """Rule key: case/whitespace-insensitive normalised merchant."""
    if not normalized or not normalized.strip():
        return None
    return _WS.sub(" ", normalized.strip().lower())[:160]
