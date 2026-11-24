# Earning referral commission on card applications (research, no outreach made)

Status: research note, 2026-11-24. Nothing here has been confirmed with a bank or a lawyer; items marked
**(verify)** need a call or a written answer before launch. Nobody was contacted.

## How money flows in UAE card distribution

1. **Direct sales agents (DSAs).** Banks pay for approved, activated cards. Historically via outsourced
   sales agencies whose agents stand in malls and call leads; pay is per approved card, often with volume
   tiers (public reporting and job ads describe monthly targets; actual rates are private contracts and not
   published **(verify)**). The CBUAE calls these "Authorized Agents".
2. **Aggregators / comparison sites** (Souqalmal, YallaCompare, Kredit, others). They earn a bounty from the
   bank per approved (sometimes per submitted) application, usually under a bilateral marketing/referral
   agreement and often by also employing or onboarding bank-approved agents who process the lead. The
   Souqalmal programmes that used to be listed on public affiliate networks (Admitad, Kwanko) are marked
   closed, so there is no open self-serve affiliate network for UAE card leads **(verify current state)**.
   Kredit runs the agent-facing model openly: verified agents (UAE PASS) get customer document links
   (kredit.ae/secure-collect), which shows that large aggregators sit on top of an agent layer rather than
   bank APIs.
3. **Bank partner / "marketing partner" programmes.** Some banks run referral or lead-gen partner
   arrangements with fintechs and employers; terms are negotiated, not published **(verify per bank)**.

I found no public evidence that the big aggregators sub-license their bank relationships to third-party apps.
Assume they would not; the realistic route is a lead-sharing deal with the aggregator or an agency, or
direct bank/agency agreements **(verify by asking, later)**.

## Rules that apply (CBUAE Consumer Protection Standards, Article 5; read on the CBUAE rulebook)

- Banks are accountable for their Authorized Agents, must run fit-and-proper checks before contracting and
  renewing, and must document agent training (5.1.1.52, 5.1.1.58, 5.1.1.80). **Consequence: a bank will
  only pay a party it has contracted and vetted; we cannot just "refer and invoice".**
- Remuneration must not be based solely or largely on sales volume or credit amount (5.1.1.62-63). Expect
  banks to cap or structure agent pay accordingly, and to ask us not to push people toward high-limit products.
- Marketing may not be misleading, must give equal prominence to key terms and fees, and promotional contact
  needs the consumer's opt-in (5.1.1.8-9, 5.1.1.68, 5.1.1.70). Suitability/affordability must be assessed
  before selling (5.1.1.13, 5.1.1.17). Cooling-off applies to the bank contract (5.1.1.28).
- Not read or verified yet **(verify)**: whether a pure lead-referral app needs its own CBUAE or DFSA/FSRA
  licence (advice and "arranging" are the risk; showing a ranked catalogue and passing an application with
  consent is the safer end), the Federal PDPL (Decree-Law 45/2021) consent rules for sharing Emirates ID and
  salary data with a bank, and Dubai DET or ADGM trade-licence activity wording for lead generation.

## Recommended path (fastest compliant)

1. **Do not touch money first.** Keep the catalogue and "best card for your spend" free and unbiased. Rank
   by the user's own spend; never let commission change the ranking (this is both the compliance posture
   and the product moat). Show a clear "we may earn a fee if you apply" line next to every apply button.
2. **Partner with an existing licensed distributor** (a bank-approved DSA agency or an aggregator) under a
   lead-sharing/revenue-share agreement, instead of becoming an agent. They carry the bank relationship,
   agent vetting and document handling; we send consented leads and receive a share when the bank approves.
   Second choice: apply as a marketing partner directly to one bank (ADCB, Mashreq, RAKBANK and ENBD are
   the likely fits for a digital-first audience) **(verify)**.
3. Start with the F&F beta, no revenue, only the tracking below, so you have conversion numbers to show a
   partner. Then approach 2 to 3 partners.

## How the app's "Apply" flow hands off a lead

Consent-first, minimal data, fully tracked:

1. User taps Apply on a card. Screen shows: which bank/partner receives the data, exactly which fields
   (name, phone, email, optional salary band, nationality; **no Emirates ID or documents from us**), the fee
   disclosure, and an unticked consent checkbox. Store the consent text version and timestamp.
2. Backend `POST /v1/leads` creates a lead row: `id, user_id, card_id, partner, consent_version,
   consented_at, fields_shared (hash + list), status, click_id, created_at`. `click_id` is a random token
   passed to the partner (URL param or API field) so approvals can be matched back.
3. Handoff, in order of preference: partner API/webhook push; partner-hosted application URL with our
   `click_id`; encrypted email to the partner's lead desk as a stop-gap. Nothing is shared before consent.
4. Status machine: `submitted -> contacted -> applied -> approved -> activated` (or `rejected`/`lost`).
   Partner reports statuses by webhook or monthly CSV; we reconcile against `click_id` and invoice on
   approved/activated per the contract.
5. User sees status in the app, can withdraw consent (stop sharing, ask the partner to delete), and gets
   one in-app notification, never SMS/WhatsApp marketing from us without separate opt-in.
6. Analytics: record impressions, taps, consent rate, lead-to-approval rate per card, never the PII itself.

## Questions to ask a lawyer or the CBUAE before real money (cheap, free to ask)

- Does referral-only lead passing, with disclosure and consent, require any licence for us?
- Is the fee for an approved card acceptable when ranking is spend-based and disclosed?
- PDPL: lawful basis and cross-border storage for lead data (Supabase Mumbai region).

## Sources

- CBUAE Rulebook, Consumer Protection Standards, Article 5 Business Conduct:
  https://rulebook.centralbank.ae/en/rulebook/article-5-business-conduct (read 2026-11-24)
- Souqalmal affiliate listing (closed programmes): https://affi.io/m/souqalmal
- Kredit agent tooling page: https://kredit.ae/secure-collect
