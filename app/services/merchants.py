import re

_WS = re.compile(r"\s+")
_TRAILING_REF = re.compile(r"[\s#*-]*\d{3,}$")


def normalize_merchant(raw: str | None) -> str | None:
    """Placeholder normalisation: collapse whitespace, drop trailing store/ref numbers, title-case."""
    if not raw:
        return None
    cleaned = _TRAILING_REF.sub("", _WS.sub(" ", raw).strip()).strip()
    return cleaned.title()[:160] or None
