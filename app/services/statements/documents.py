"""File -> StatementDocument: PDF decryption (pikepdf), text/table extraction (pdfplumber), CSV rows."""

import csv
import io
from collections.abc import Iterable

import pdfplumber
import pikepdf

from app.services.parsers.statement_base import StatementDocument, Table

MAX_PAGES = 60


class DocumentError(Exception):
    """The file is not a readable statement document."""


class PasswordRequired(DocumentError):
    """Encrypted PDF and no candidate password was available."""


class PasswordInvalid(DocumentError):
    """Encrypted PDF and none of the candidate passwords opened it."""


def sniff_kind(data: bytes) -> str:
    """'pdf' | 'csv' | 'unknown' from the first bytes (never trust the client's content type)."""
    if data[:5] == b"%PDF-":
        return "pdf"
    head = data[:4096]
    if b"\x00" in head:
        return "unknown"
    try:
        head.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            head.decode("cp1252")
        except UnicodeDecodeError:
            return "unknown"
    return "csv" if (b"," in head or b";" in head or b"\t" in head) and b"\n" in head else "unknown"


def decrypt_pdf(data: bytes, passwords: Iterable[str] = ()) -> bytes:
    """Return an unencrypted copy. Raises PasswordRequired / PasswordInvalid for encrypted files."""
    candidates = [p for p in dict.fromkeys(passwords) if p]
    try:
        with pikepdf.open(io.BytesIO(data)) as pdf:  # empty user password (owner-password-only files)
            if not pdf.is_encrypted:
                return data
            return _save(pdf)
    except pikepdf.PasswordError:
        pass
    except pikepdf.PdfError as exc:
        raise DocumentError(f"Unreadable PDF: {exc}") from exc
    for pw in candidates:
        try:
            with pikepdf.open(io.BytesIO(data), password=pw) as pdf:
                return _save(pdf)
        except pikepdf.PasswordError:
            continue
        except pikepdf.PdfError as exc:
            raise DocumentError(f"Unreadable PDF: {exc}") from exc
    raise (
        PasswordInvalid("None of the stored passwords opened the PDF")
        if candidates
        else PasswordRequired("PDF is password protected")
    )


def _save(pdf: pikepdf.Pdf) -> bytes:
    out = io.BytesIO()
    pdf.save(out)  # saving without an encryption argument writes an unencrypted copy
    return out.getvalue()


def extract_pdf(data: bytes) -> StatementDocument:
    """Text (layout-preserving per line) and ruled tables from an unencrypted PDF."""
    texts: list[str] = []
    tables: list[Table] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = pdf.pages[:MAX_PAGES]
            for page in pages:
                texts.append(page.extract_text(x_tolerance=2, y_tolerance=3) or "")
                for t in page.extract_tables():
                    tables.append([[c for c in row] for row in t])
            n = len(pdf.pages)
    except Exception as exc:  # pdfminer raises a zoo of exceptions on corrupt input
        raise DocumentError(f"Could not read PDF text: {exc}") from exc
    return StatementDocument(text="\n".join(texts), tables=tables, pages=n)


def extract_csv(data: bytes) -> StatementDocument:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows: Table = [[c for c in row] for row in csv.reader(io.StringIO(text), dialect)]
    flat = "\n".join(" ".join(c for c in row if c) for row in rows)
    return StatementDocument(text=flat, tables=[rows], pages=1, is_csv=True)
