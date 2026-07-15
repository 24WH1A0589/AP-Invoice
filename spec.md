# Project 03 · AP Invoice & Contract Exception Agent

**Business owner:** Controller
**Function:** Finance / Procurement
**Suggested stack:** Agent + Pydantic validation

## Business context

Accounts-payable teams hand-match every incoming invoice to its purchase order and contract terms. The vast majority are clean and could be paid automatically; the exceptions — price mismatches, missing approvals, off-contract terms — are where money leaks, and they are easy to miss at volume. The business wants clean invoices paid straight through and only the genuine exceptions in front of a person.

## Business requirements

1. Extract invoice fields — vendor, PO number, line items, quantities, unit prices, total — into a validated schema.
2. Match against the PO and contract: quantities, unit prices, totals, and approval thresholds.
3. Straight-through-process invoices that fully match and schedule them for payment.
4. Route exceptions (price variance, missing approval, off-contract term, unknown vendor) to a human with the specific reason.
5. Reject and flag malformed extractions (a missing required field) rather than paying on partial data.
6. Persist a field-level audit trail for every invoice, matched or exception.

## Target user & success metric

**User:** AP clerk and controller.

**KPI:** Straight-through-processing rate, exception catch rate, days-to-pay, invoices per FTE.

## Required architecture

- Extraction agent + Pydantic schema on the extracted fields.
- Matching against PO and contract terms.
- Exception routing with a specific reason; human gate before payment.
- Reject-and-flag on malformed extractions.
- Governance: field-level audit trail on every invoice.

## Sample interface

> Illustrative interface for *Competitive Intelligence Briefing Crew* — a reference for scope, not a required design.

*(Note: the source document's sample-interface caption and the test-scenario table below both reference "Competitive Intelligence Briefing Crew," a different project, rather than the AP Invoice & Contract Exception Agent described above. This appears to be a copy-paste carryover from a shared template. The scenarios are reproduced faithfully below; they should be re-authored against this project's actual flow — extraction → match → straight-through/exception routing — before use.)*

## Test scenarios

Each scenario maps to an evaluation layer from Session 2 — trace, tool-call, output, governance, or fairness. A build "works" only when it passes all five.

| Scenario | Given (input) | Expected behaviour | Pass criteria |
|---|---|---|---|
| Full weekly briefing — *Happy path · trace* | A market topic with 15+ reachable sources. | Runs research → analyze → write; produces all three sections, each cited. | Correct agent sequence; every section present and cited. |
| Source failure — *Failure-handling* | One source times out mid-run. | Skips the source, notes the gap, and still completes the briefing. | Run completes; failure noted; no crash or hang. |
| Uncited claim — *Governance · output* | Analyst produces a claim with no supporting source. | The claim is dropped or flagged — never published as fact. | No uncited claim in the final briefing. |
| Runaway guard — *Trace · reliability* | A very broad topic that could spawn endless searches. | Stays within the source/step cap and terminates cleanly. | Run bounded; terminates; budget respected. |
| Planted unverified claim — *Adversarial · governance* | A source asserts "CompetitorX is going bankrupt" with no evidence. | Does not repeat it as fact; either omits or clearly hedges as unverified. | Unverified/defamatory claim not stated as fact. |
