"""
Unit tests for ExceptionRouter — no LLM calls, no I/O.

Covers every routing branch:
  STRAIGHT_THROUGH_PAY  — clean MatchResult
  HUMAN_REVIEW          — exceptions present (price variance, missing approval, etc.)
  REJECT                — invoice already has REJECTED status (malformed extraction)

Also verifies:
  - reviewer_notes are populated for HUMAN_REVIEW decisions
  - priority ordering (UNKNOWN_VENDOR > PRICE_VARIANCE)
  - exception list is propagated faithfully to RouteDecision
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ap_agent.matching import MatchResult
from ap_agent.router import RoutingAction
from ap_agent.schemas import ExceptionReason, InvoiceStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_match(invoice_number: str = "INV-001") -> MatchResult:
    return MatchResult(
        invoice_number=invoice_number,
        status=InvoiceStatus.MATCHED,
        exceptions=[],
        details={},
    )


def _exception_match(
    *reasons: ExceptionReason,
    invoice_number: str = "INV-001",
    details: dict | None = None,
) -> MatchResult:
    return MatchResult(
        invoice_number=invoice_number,
        status=InvoiceStatus.EXCEPTION,
        exceptions=list(reasons),
        details=details or {r.value: f"Detail for {r.value}" for r in reasons},
    )


# ---------------------------------------------------------------------------
# STRAIGHT_THROUGH_PAY
# ---------------------------------------------------------------------------

class TestStraightThroughPay:
    def test_clean_match_routes_to_pay(self, router, make_invoice):
        inv = make_invoice()
        decision = router.route(inv, _clean_match(inv.invoice_number))
        assert decision.action == RoutingAction.STRAIGHT_THROUGH_PAY

    def test_pay_decision_has_zero_priority(self, router, make_invoice):
        inv = make_invoice()
        decision = router.route(inv, _clean_match(inv.invoice_number))
        assert decision.priority == 0

    def test_pay_decision_no_exceptions(self, router, make_invoice):
        inv = make_invoice()
        decision = router.route(inv, _clean_match(inv.invoice_number))
        assert decision.exceptions == []

    def test_pay_decision_no_reviewer_notes(self, router, make_invoice):
        inv = make_invoice()
        decision = router.route(inv, _clean_match(inv.invoice_number))
        assert decision.reviewer_notes == ""

    def test_pay_decision_invoice_number_matches(self, router, make_invoice):
        inv = make_invoice(invoice_number="INV-5551")
        decision = router.route(inv, _clean_match("INV-5551"))
        assert decision.invoice_number == "INV-5551"


# ---------------------------------------------------------------------------
# HUMAN_REVIEW — Scenario 2 (price variance)
# ---------------------------------------------------------------------------

class TestHumanReviewPriceVariance:
    def test_price_variance_routes_to_review(self, router, make_invoice):
        inv = make_invoice(unit_price=Decimal("42.00"))
        match = _exception_match(ExceptionReason.PRICE_VARIANCE)
        decision = router.route(inv, match)
        assert decision.action == RoutingAction.HUMAN_REVIEW

    def test_price_variance_in_exceptions_list(self, router, make_invoice):
        inv = make_invoice(unit_price=Decimal("42.00"))
        match = _exception_match(ExceptionReason.PRICE_VARIANCE)
        decision = router.route(inv, match)
        assert ExceptionReason.PRICE_VARIANCE in decision.exceptions

    def test_price_variance_reviewer_notes_populated(self, router, make_invoice):
        inv = make_invoice(unit_price=Decimal("42.00"))
        match = _exception_match(ExceptionReason.PRICE_VARIANCE)
        decision = router.route(inv, match)
        assert len(decision.reviewer_notes) > 0

    def test_price_variance_reviewer_notes_mention_exception(self, router, make_invoice):
        inv = make_invoice(unit_price=Decimal("42.00"))
        match = _exception_match(ExceptionReason.PRICE_VARIANCE)
        decision = router.route(inv, match)
        assert "price_variance" in decision.reviewer_notes.lower()

    def test_not_scheduled_for_payment(self, router, make_invoice):
        inv = make_invoice(unit_price=Decimal("42.00"))
        match = _exception_match(ExceptionReason.PRICE_VARIANCE)
        decision = router.route(inv, match)
        assert decision.action != RoutingAction.STRAIGHT_THROUGH_PAY


# ---------------------------------------------------------------------------
# HUMAN_REVIEW — Scenario 3 (missing approval)
# ---------------------------------------------------------------------------

class TestHumanReviewMissingApproval:
    def test_missing_approval_routes_to_review(self, router, make_invoice):
        inv = make_invoice(po_number="PO-5552", quantity=Decimal("400"))
        match = _exception_match(ExceptionReason.MISSING_APPROVAL)
        decision = router.route(inv, match)
        assert decision.action == RoutingAction.HUMAN_REVIEW

    def test_missing_approval_reason_propagated(self, router, make_invoice):
        inv = make_invoice(po_number="PO-5552", quantity=Decimal("400"))
        match = _exception_match(ExceptionReason.MISSING_APPROVAL)
        decision = router.route(inv, match)
        assert ExceptionReason.MISSING_APPROVAL in decision.exceptions

    def test_missing_approval_held_not_paid(self, router, make_invoice):
        inv = make_invoice(po_number="PO-5552", quantity=Decimal("400"))
        match = _exception_match(ExceptionReason.MISSING_APPROVAL)
        decision = router.route(inv, match)
        assert decision.action != RoutingAction.STRAIGHT_THROUGH_PAY

    def test_missing_approval_priority_above_price_variance(self, router, make_invoice):
        """MISSING_APPROVAL (75) must outrank PRICE_VARIANCE (50) in priority."""
        inv = make_invoice()
        match_approval = _exception_match(ExceptionReason.MISSING_APPROVAL)
        match_price = _exception_match(ExceptionReason.PRICE_VARIANCE)
        d_approval = router.route(inv, match_approval)
        d_price = router.route(inv, match_price)
        assert d_approval.priority > d_price.priority


# ---------------------------------------------------------------------------
# REJECT — Scenario 4 (malformed extraction)
# ---------------------------------------------------------------------------

class TestRejectMalformed:
    def test_rejected_invoice_routes_to_reject(self, router, make_invoice):
        inv = make_invoice(status=InvoiceStatus.REJECTED)
        match = _clean_match(inv.invoice_number)   # match is irrelevant for REJECTED
        decision = router.route(inv, match)
        assert decision.action == RoutingAction.REJECT

    def test_reject_has_max_priority(self, router, make_invoice):
        inv = make_invoice(status=InvoiceStatus.REJECTED)
        decision = router.route(inv, _clean_match(inv.invoice_number))
        assert decision.priority == 100

    def test_reject_not_paid(self, router, make_invoice):
        inv = make_invoice(status=InvoiceStatus.REJECTED)
        decision = router.route(inv, _clean_match(inv.invoice_number))
        assert decision.action != RoutingAction.STRAIGHT_THROUGH_PAY

    def test_reject_reviewer_notes_present(self, router, make_invoice):
        """Reviewer notes must explain why the invoice was rejected."""
        inv = make_invoice(status=InvoiceStatus.REJECTED)
        decision = router.route(inv, _clean_match(inv.invoice_number))
        assert len(decision.reviewer_notes) > 0


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------

class TestPriorityOrdering:
    """Higher-severity exceptions must produce higher priority scores."""

    def test_unknown_vendor_outranks_price_variance(self, router, make_invoice):
        inv = make_invoice()
        d_vendor = router.route(inv, _exception_match(ExceptionReason.UNKNOWN_VENDOR))
        d_price = router.route(inv, _exception_match(ExceptionReason.PRICE_VARIANCE))
        assert d_vendor.priority > d_price.priority

    def test_po_not_found_outranks_quantity_variance(self, router, make_invoice):
        inv = make_invoice()
        d_po = router.route(inv, _exception_match(ExceptionReason.PO_NOT_FOUND))
        d_qty = router.route(inv, _exception_match(ExceptionReason.QUANTITY_VARIANCE))
        assert d_po.priority > d_qty.priority

    def test_multiple_exceptions_use_max_priority(self, router, make_invoice):
        """When multiple exceptions present, priority = max of individual priorities."""
        inv = make_invoice()
        match = _exception_match(
            ExceptionReason.QUANTITY_VARIANCE,   # 40
            ExceptionReason.MISSING_APPROVAL,    # 75
        )
        decision = router.route(inv, match)
        assert decision.priority == 75
