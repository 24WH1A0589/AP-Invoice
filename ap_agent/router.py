"""
Exception router — decides what to do with each invoice after matching.

Decision tree
-------------
REJECTED  → extraction was malformed; no payment, immediate flag.
MATCHED   → all checks passed; schedule for straight-through payment.
EXCEPTION → one or more rules failed; build a structured review packet
            and put it in the human-review queue.

The router does NOT make payment — it returns a RouteDecision that the
orchestrator acts on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .matching import MatchResult
from .schemas import ExceptionReason, Invoice, InvoiceStatus


class RoutingAction(str, Enum):
    STRAIGHT_THROUGH_PAY = "straight_through_pay"
    HUMAN_REVIEW = "human_review"
    REJECT = "reject"


# Priority ordering for exceptions — higher index = more urgent
_PRIORITY: dict[ExceptionReason, int] = {
    ExceptionReason.MALFORMED_EXTRACTION: 100,
    ExceptionReason.UNKNOWN_VENDOR: 90,
    ExceptionReason.PO_NOT_FOUND: 85,
    ExceptionReason.CONTRACT_NOT_FOUND: 80,
    ExceptionReason.MISSING_APPROVAL: 75,
    ExceptionReason.EXCEEDS_THRESHOLD: 70,
    ExceptionReason.OFF_CONTRACT_TERM: 60,
    ExceptionReason.PRICE_VARIANCE: 50,
    ExceptionReason.TOTAL_MISMATCH: 45,
    ExceptionReason.QUANTITY_VARIANCE: 40,
}


@dataclass
class RouteDecision:
    """Result of routing an invoice."""

    invoice_number: str
    action: RoutingAction
    priority: int = 0  # 0 = low, 100 = critical
    summary: str = ""
    exceptions: list[ExceptionReason] = field(default_factory=list)
    exception_details: dict = field(default_factory=dict)
    reviewer_notes: str = ""  # pre-populated guidance for the human reviewer


class ExceptionRouter:
    """
    Stateless router.  Call route() with an Invoice and its MatchResult.
    """

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def route(self, invoice: Invoice, match_result: MatchResult) -> RouteDecision:
        """
        Produce a RouteDecision based on invoice status and match exceptions.
        """
        # Case 1: malformed extraction — reject immediately
        if invoice.status == InvoiceStatus.REJECTED:
            return RouteDecision(
                invoice_number=invoice.invoice_number,
                action=RoutingAction.REJECT,
                priority=100,
                summary="Invoice rejected: extraction failed or schema validation error.",
                exceptions=invoice.exception_reasons,
                exception_details={
                    "raw_text_snippet": (invoice.raw_text or "")[:300]
                },
                reviewer_notes=(
                    "The raw invoice text could not be parsed into a valid schema. "
                    "Manually key the invoice or request a corrected document from the vendor."
                ),
            )

        # Case 2: clean match — straight-through payment
        if match_result.is_clean:
            return RouteDecision(
                invoice_number=invoice.invoice_number,
                action=RoutingAction.STRAIGHT_THROUGH_PAY,
                priority=0,
                summary=(
                    f"Invoice {invoice.invoice_number} fully matched. "
                    f"Total {invoice.currency} {invoice.total:.2f} scheduled for payment."
                ),
                exceptions=[],
                exception_details={},
                reviewer_notes="",
            )

        # Case 3: exceptions found — route to human
        priority = self._compute_priority(match_result.exceptions)
        notes = self._build_reviewer_notes(invoice, match_result)

        return RouteDecision(
            invoice_number=invoice.invoice_number,
            action=RoutingAction.HUMAN_REVIEW,
            priority=priority,
            summary=(
                f"Invoice {invoice.invoice_number} has {len(match_result.exceptions)} "
                f"exception(s) requiring review."
            ),
            exceptions=match_result.exceptions,
            exception_details=match_result.details,
            reviewer_notes=notes,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_priority(exceptions: list[ExceptionReason]) -> int:
        if not exceptions:
            return 0
        return max(_PRIORITY.get(e, 0) for e in exceptions)

    @staticmethod
    def _build_reviewer_notes(invoice: Invoice, match_result: MatchResult) -> str:
        lines = [
            f"Invoice: {invoice.invoice_number}",
            f"Vendor:  {invoice.vendor_name} ({invoice.vendor_id})",
            f"PO:      {invoice.po_number}",
            f"Total:   {invoice.currency} {invoice.total:.2f}",
            "",
            "Exceptions detected:",
        ]

        for i, exc in enumerate(match_result.exceptions, 1):
            detail = match_result.details.get(
                # try exact key or any key that contains the enum value
                exc.value,
                next(
                    (v for k, v in match_result.details.items() if exc.value in k),
                    "See exception_details for more information.",
                ),
            )
            lines.append(f"  {i}. [{exc.value.upper()}] {detail}")

        lines += [
            "",
            "Action required: Review the flagged items and either approve, "
            "request a credit note, or reject the invoice.",
        ]
        return "\n".join(lines)
