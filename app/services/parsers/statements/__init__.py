"""Statement parsers: one module per institution (``PARSER``) + a bank-agnostic fallback chain."""

from dataclasses import dataclass

from app.services.institutions import institutions
from app.services.parsers.statement_base import (
    ParsedStatement,
    StatementDocument,
    StatementKind,
    StatementParser,
)
from app.services.parsers.statements import (
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
from app.services.parsers.statements.layout import GenericLayoutParser, detect_kind
from app.services.parsers.statements.tables import parse_tables

BANK_PARSERS: dict[str, StatementParser] = {
    p.institution: p
    for p in (
        enbd.PARSER, adcb.PARSER, fab.PARSER, mashreq.PARSER, rakbank.PARSER, dib.PARSER,
        adib.PARSER, hsbc.PARSER, citi.PARSER, cbd.PARSER, eib.PARSER, liv.PARSER,
    )
}  # fmt: skip
_GENERIC = GenericLayoutParser()


@dataclass(frozen=True, slots=True)
class ParseOutcome:
    statement: ParsedStatement | None
    institution: str | None
    kind: StatementKind
    attempts: list[str]


def reconciles(stmt: ParsedStatement) -> bool:
    from app.services.statements.reconcile import compute_reconciliation

    return (
        compute_reconciliation(stmt.kind, stmt.opening_minor, stmt.closing_minor, stmt.lines).status
        == "matched"
    )


def parse_document(
    doc: StatementDocument, *, institution: str | None = None, kind: StatementKind | None = None
) -> ParseOutcome:
    """Bank parser (explicit hint, else detected from the text) -> generic layout -> table heuristic.

    The first candidate whose lines reconcile against the printed balances wins; otherwise the
    candidate with the most lines does (reconciliation then reports the mismatch).
    """
    inst = institutions.resolve_key(institution) or institutions.detect_in_text(doc.text)
    kind = kind or (detect_kind(doc.text) if doc.text.strip() else "bank_account")
    candidates: list[tuple[str, ParsedStatement]] = []
    attempts: list[str] = []

    def attempt(name: str, result: ParsedStatement | None) -> bool:
        attempts.append(f"{name}:{len(result.lines) if result else 0}")
        if result is None or not result.lines:
            return False
        result.parser = name
        candidates.append((name, result))
        return reconciles(result)

    # Ruled tables (when the PDF has them) carry cleaner descriptions than the text layout, so they go first.
    order: list[str] = (
        ["tables", "bank", "generic"] if (doc.tables and not doc.is_csv) else ["bank", "generic", "tables"]
    )
    if doc.is_csv:
        order = ["tables"]
    for step in order:
        if step == "bank" and inst and inst in BANK_PARSERS and doc.text.strip():
            done = attempt(f"{inst}:{kind}", BANK_PARSERS[inst].parse(doc, kind))
        elif step == "generic" and doc.text.strip():
            done = attempt(f"generic-layout:{kind}", _GENERIC.parse(doc, kind))
        elif step == "tables":
            done = attempt(f"tables:{kind}", parse_tables(doc, kind))
        else:
            continue
        if done:
            break

    if not candidates:
        return ParseOutcome(None, inst, kind, attempts)
    best = (
        next((c for _, c in candidates if reconciles(c)), None)
        or max(candidates, key=lambda c: len(c[1].lines))[1]
    )
    if inst:
        best.institution = inst
    return ParseOutcome(best, inst, kind, attempts)


__all__ = ["BANK_PARSERS", "ParseOutcome", "parse_document", "reconciles"]
