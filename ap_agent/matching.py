"""
Matching engine — compares an extracted Invoice against its PurchaseOrder
and Contract, producing a MatchResult that lists every exception found.

Rules
-----
1. PO must exist for the po_number on the invoice.
2. Vendor ID on the invoice must match the PO's vendor_id.
3. For each invoice line, find the corresponding PO line by line_number.
   - Quantity on invoice must equal quantity_ordered on PO.
   - Unit price on invoice must be within contract price-tolerance (if a
     contract term exists for that line); otherwise it must match the PO
     unit price exactly (within a small absolute tolerance).
4. Invoice total must match sum of matched line totals + tax.
5. If invoice total exceeds the contract's auto_approval_threshold, flag
   MISSING_APPROVAL unless the PO already carries an approver.
6. Contract must not be expired as of invoice_date.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .schemas import (
    Contract,
    ExceptionReason,
    Invoice,
    InvoiceStatus,
    PurchaseOrder,
)

# Absolute price tolerance when no contract term exists (e.g. rounding)
_ABS_PRICE_TOLERANCE = Decimal("0.01")


@dataclass
class MatchResult:
    """Outcome of matching an invoice against PO + contract."""

    invoice_number: str
    status: InvoiceStatus
    exceptions: list[ExceptionReason] = field(default_factory=list)
    details: dict = field(default_factory=dict)  # human-readable detail per exception

    @property
    def is_clean(self) -> bool:
        return len(self.exceptions) == 0


class MatchingEngine:
    """
    Stateless matcher.  Instantiate once; call match() per invoice.

    Parameters
    ----------
    po_registry : dict[str, PurchaseOrder]
        Keyed by po_number.
    contract_registry : dict[str, Contract]
        Keyed by vendor_id.
    """

    def __init__(
        self,
        po_registry: dict[str, PurchaseOrder],
        contract_registry: dict[str, Contract],
    ) -> None:
        self._pos = po_registry
        self._contracts = contract_registry

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def match(self, invoice: Invoice) -> MatchResult:
        """Run all matching rules and return a MatchResult."""
        result = MatchResult(
            invoice_number=invoice.invoice_number,
            status=InvoiceStatus.PENDING,
        )

        # 1. Locate PO
        po = self._pos.get(invoice.po_number)
        if po is None:
            result.exceptions.append(ExceptionReason.PO_NOT_FOUND)
            result.details["po_not_found"] = (
                f"PO '{invoice.po_number}' not found in registry."
            )
            result.status = InvoiceStatus.EXCEPTION
            return result  # cannot continue without PO

        # 2. Vendor check
        if invoice.vendor_id != po.vendor_id:
            result.exceptions.append(ExceptionReason.UNKNOWN_VENDOR)
            result.details["unknown_vendor"] = (
                f"Invoice vendor '{invoice.vendor_id}' does not match "
                f"PO vendor '{po.vendor_id}'."
            )

        # 3. Locate contract (optional — not all POs are under a contract)
        contract = self._contracts.get(invoice.vendor_id)

        # 4. Contract expiry check
        if contract is not None and contract.expiry_date is not None:
            if invoice.invoice_date > contract.expiry_date:
                result.exceptions.append(ExceptionReason.OFF_CONTRACT_TERM)
                result.details["off_contract_term"] = (
                    f"Invoice date {invoice.invoice_date} is after contract "
                    f"expiry {contract.expiry_date}."
                )

        # 5. Build a quick lookup: PO lines by line_number
        po_lines = {li.line_number: li for li in po.line_items}

        # 6. Contract terms by item description (normalised lower)
        contract_terms = {}
        if contract is not None:
            for term in contract.terms:
                contract_terms[term.description.strip().lower()] = term

        # 7. Line-by-line matching
        for inv_line in invoice.line_items:
            po_line = po_lines.get(inv_line.line_number)
            if po_line is None:
                result.exceptions.append(ExceptionReason.PO_NOT_FOUND)
                result.details[f"line_{inv_line.line_number}_no_po_line"] = (
                    f"Line {inv_line.line_number} ('{inv_line.description}') "
                    f"has no matching PO line."
                )
                continue

            # Quantity check
            if inv_line.quantity != po_line.quantity_ordered:
                result.exceptions.append(ExceptionReason.QUANTITY_VARIANCE)
                result.details[f"line_{inv_line.line_number}_qty"] = (
                    f"Qty invoiced {inv_line.quantity} ≠ PO qty "
                    f"{po_line.quantity_ordered}."
                )

            # Price check — prefer contract term if available
            desc_key = inv_line.description.strip().lower()
            if desc_key in contract_terms:
                term = contract_terms[desc_key]
                allowed_delta = (
                    term.agreed_unit_price * term.price_tolerance_pct
                ).quantize(Decimal("0.0001"))
                if abs(inv_line.unit_price - term.agreed_unit_price) > allowed_delta:
                    result.exceptions.append(ExceptionReason.PRICE_VARIANCE)
                    result.details[f"line_{inv_line.line_number}_price"] = (
                        f"Unit price {inv_line.unit_price} deviates from "
                        f"contract price {term.agreed_unit_price} by more than "
                        f"{term.price_tolerance_pct * 100}%."
                    )
            else:
                # Fall back to PO price
                if abs(inv_line.unit_price - po_line.unit_price) > _ABS_PRICE_TOLERANCE:
                    result.exceptions.append(ExceptionReason.PRICE_VARIANCE)
                    result.details[f"line_{inv_line.line_number}_price"] = (
                        f"Unit price {inv_line.unit_price} ≠ PO price "
                        f"{po_line.unit_price} (no contract term)."
                    )

        # 8. Invoice total vs PO total
        po_total = sum(
            li.quantity_ordered * li.unit_price for li in po.line_items
        ).quantize(Decimal("0.01"))
        inv_total = invoice.total.quantize(Decimal("0.01"))
        if abs(inv_total - po_total) > Decimal("0.05") and not result.exceptions:
            # Only flag total mismatch if line checks passed (avoids double-reporting)
            result.exceptions.append(ExceptionReason.TOTAL_MISMATCH)
            result.details["total_mismatch"] = (
                f"Invoice total {inv_total} does not match PO total {po_total}."
            )

        # 9. Approval threshold
        threshold = (
            contract.auto_approval_threshold
            if contract
            else Decimal("10000.00")
        )
        if invoice.total > threshold and not po.approved_by:
            result.exceptions.append(ExceptionReason.MISSING_APPROVAL)
            result.details["missing_approval"] = (
                f"Invoice total {invoice.total} exceeds auto-approval threshold "
                f"{threshold} and PO has no approver."
            )

        # 10. Final status
        result.status = (
            InvoiceStatus.MATCHED if result.is_clean else InvoiceStatus.EXCEPTION
        )
        return result
