# Statement layouts

Structure only, no personal data. Written for the Dart on-device port: the Python parsers are the reference
and every rule below maps to code in `app/services/parsers/` (`statements/mashreq.py`, `counterparty.py`,
`statements/money.py`). The tests build synthetic PDFs with the same structure (`tests/neo_fixture.py`).

## Identified formats

| Institution / product | Kind | Verified on a real file | Parser |
|---|---|---|---|
| Mashreq NEO, "Consolidated Relationship Statement" (current + savings accounts in one PDF) | bank accounts | yes (two monthly files) | `mashreq.parse_account_sections` |
| Mashreq cards | credit card | no (generic layout, documented wording only) | `mashreq.CARD` layout |

The PDFs are not encrypted, have six pages and are produced by a statement-composition engine; text extracts
with pdfplumber (`extract_text(x_tolerance=2, y_tolerance=3)`). No card statement (due date, minimum due,
"Cr"/"Dr" suffixes, foreign-currency lines) has been seen yet, so those paths are still the generic layout.

## Page structure (Mashreq NEO consolidated PDF)

1. **Page 1, summary.** Title `Consolidated Relationship Statement` (+ Arabic twin), holder name on the next
   line, then `Customer Number <n>`, `Issue Date dd/mm/yyyy`, `Statement Period dd/mm/yyyy to dd/mm/yyyy`.
   Tables `Current Account Summary` / `Savings Account Summary`: a row is
   `[product] <account no> <CCY> <foreign bal> <AED balance> <monthly avg> [overdraft] <rate>% [available]`, then
   `IBAN: AE..`. The product name may wrap around the number row (`Neo Current` / row / `Account`). The
   summary AED balance is the balance on the *issue date*, not the period closing balance: do not reconcile
   against it.
2. **One section per account**, each starting a new page, repeated on every page of that section:
   page header (holder, address block, `e - statement` printed vertically, which extracts as the lines
   `tnemetats`, `-`, `e`), title `<Savings|Current> Account Statement` (+ Arabic), `Account No <n> <CCY>`,
   `IBAN AE..`, `Customer Number`, regulator sentence, `Statement for Period dd/mm/yyyy to dd/mm/yyyy`, the
   table header `Date Transaction Reference no Debit Credit Balance`, then a line of unreadable Arabic
   headings (extraction noise).
3. **Last pages**: a boilerplate "How to read your bank statement" page and a page of Arabic text. Ignored
   because no table header precedes them.
4. Every page ends with `Page n of m`.

A section is keyed by its account number: the second page of a section re-enters the same key.

## Table rules

* **Amounts have three decimals** (`6,000.000`, mills digit always 0). Regex
  `\d{1,3}(?:,\d{3})*\.\d{3}(?!\d)|\d+\.\d{3}(?!\d)`; value = decimal * 100 (rounded) = minor units. No
  currency symbol, no Cr/Dr, no negative numbers. Overdrawn balances were not seen.
* A **dated row** is `dd/mm/yyyy [text] <reference> <debit> <credit> <balance>`; the last three tokens are
  always the three money columns, exactly one of debit/credit is non-zero. Direction = which column is
  non-zero (cross-checked against the balance delta; a mismatch only adds a warning). Row regex:
  `^(\d{2}/\d{2}/\d{4})\s*(.*?)\s+(m3)\s+(m3)\s+(m3)$`.
* **Reference no**: `\d{3}[A-Z]{4}[A-Za-z0-9]{3,}(?: \d{5})?` at the end of the text before the amounts (one
  family ends with a separate 5-digit counter after a space). Characters 4-7 name the transaction family:
  `AACT` account-to-account, `IPPD` instant payment, `WPSP` salary (WPS), `IWRT` inward remittance, `CLCH`
  cardless cash, `FSBI` quick remittance, `DCRD` local fund transfer, `MCIT` interest. Both legs of an
  own-account transfer carry the same reference in the two sections (use it to pair them).
* **Opening/closing**: `OPENING BALANCE 0.000 0.000 <bal>` and `CLOSING BALANCE 0.000 0.000 <bal>`. A section
  with no opening row is not reconcilable. `Balance carried forward <bal>` (last row of a page) and
  `Balance brought forward <bal>` (first row of the next page) are page-break bridges: they are neither a
  transaction nor the opening/closing; take them only as a running-balance check.
* **Descriptions are vertically centred multi-line cells.** The dated row carries one line of the
  description *in the middle*; wrapped text sits above and below it, so a naive "continuation lines follow
  the row" parser mis-assigns it. Filler lines `<text> 0.000 0.000 0.000` (no date) are description lines too
  (the zeros are noise): strip the zeros, keep the text.
* **Splitting the text between two dated rows** (lines in between are the gap `G`; first part belongs to the
  previous row as trailing text, the rest leads the next row):
  1. Lines before a page-break bridge or `CLOSING BALANCE` all trail the previous row; lines after
     `OPENING BALANCE` / `brought forward` and before the first row all lead it.
  2. Never split before the last filler line of `G`, nor before continuation lines after it
     (`^ACCOUNT No:`, `^Transaction Ref#`, `^T_[0-9a-f]{8,}`).
  3. Otherwise split before the first line matching an entry starter
     `^(VALUE DATE:|IPP TRANSFER\b|QuickRemit\b|Cardless Cash\b)`.
  4. Otherwise the previous row printed `p` lines above its dated line and prints `p` or `p+1` below: trail
     `min(|G|, p+1)` lines (and `0` if `p = 0`). Unknown families fall back to this rule; extend the starter
     list when a new one appears.
  Geometry is a better signal when the port has coordinates: description lines sit on a 9pt grid and entries
  are separated by about 12pt (an extra 3pt), which splits entries exactly.
* Description = lead + text between date and reference + trail, joined with single spaces, with
  `VALUE DATE: dd-mm-yyyy` removed. A row with no text at all (seen on own-account transfers) gets a label
  from the reference family.
* **Noise to drop**: lines containing `(cid:` or mostly non-ASCII characters (Arabic headings), except
  English labels that merely carry an Arabic suffix (`<Type> Account Statement`, `Account No`, `IBAN`,
  `Customer Number`, `Issue Date`, `Statement for Period`); header lines between a page footer and the table
  header.
* Reconciliation per section: `opening + sum(credits) - sum(debits) == closing`, both printed with the mills
  digit dropped. Both real files reconcile exactly for both accounts.

## What a multi-account PDF becomes

`parse_document` returns `ParseOutcome.sections` (one `ParsedStatement` per account, `statement` is the first
with lines). The pipeline stores the first section on the uploaded `statements` row and one extra row per other
section (`external_id = <statement id>:section:<n>`, no file hash), each resolved to its account by the last 4
digits of the IBAN (account number as fallback) and reconciled on its own.

## Person-to-person transfers (`counterparty`)

Statement lines expose `counterparty = {name, direction, phone?, iban_tail?}` for transfers to or from an
individual. `direction` is `outgoing` for debits and `incoming` for credits; `phone` is E.164; `iban_tail`
is the last four digits of the beneficiary IBAN or account number. Rules (all case-insensitive, applied to
the assembled description):

1. Return nothing when the text contains `credit card payment`, `card payment`, `own account`,
   `account to account`, `cardless cash`, `ATM` or `salary`, or the account part is a masked card
   (`\d{4,}X{4,}\d{4}`).
2. Instant payment (Aani/IPP): `<12-digit prefix><IBAN> - <NAME> - /REF/ <purpose>`; IBAN is
   `AE\d{2}\d{19}`; name = text between the account token and `/REF/`.
3. Quick remittance: `QuickRemit <n> to A/C <acct> - <NAME> With Transaction Ref#...`.
4. Local fund transfer: `Local Fund Transfer, <NAME>, A/C No: <acct>`.
5. Inward remittance: `Inward Remittance <SENDER> <BANK NAME>`; the bank starts one word before its first
   bank keyword (`BANK|LIMITED|LTD|PJSC|PLC|LLC|BRANCH|N.A.|EXCHANGE|FINANCE`).
6. Mobile (Aani by number): UAE mobile `(?:\+|00)?(?:971[\s-]?|0)5[024568][\s-]?\d{3}[\s-]?\d{4}` -> `phone`;
   name = words after `to|from|beneficiary|sender|ben|by`, else the words before the number.
7. Names are cut at the first digit or noise word (`AED`, `REF`, `transfer`, ...). A candidate with neither
   phone, IBAN tail nor remittance shape is dropped.
8. Own filter: banks truncate names, so a name is the holder when every token (3+ chars) is a prefix of, or
   prefixed by, a token of the holder name printed on the statement. The holder name is read from the line
   after the title and is never stored.

Edge cases seen: credit card bills are paid with the same `IPP TRANSFER` shape (masked card number, holder's
own name) and must stay out; own-account transfers use the holder's full name; inward remittances list the
sender and the remitting bank on separate lines. Incoming instant-payment and mobile-number wording was not
available in the real files; rules 2 and 6 are written for the symmetrical text and need a real sample.

## Generic layout (other banks, cards)

Unchanged: `statements/layout.py` documents the card/account row format (`Cr`/`Dr`/leading `-` marks,
foreign amount + currency before the AED amount, `Payment Due Date`, `Minimum Payment Due`, summary tables).
To add a bank, give it a `Layout` in its module; add a dedicated section parser only when the table is a
multi-line cell like the one above.
