"""
Pydantic v2 schemas for AP Invoice & Contract Exception Agent.
Covers: LineItem, Invoice, PurchaseOrder, Contract, AuditTrail.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class InvoiceStatus(str, Enum):
    PENDING = "pending"
    MATCHED = "matched"
    EXCEPTION = "exception"
    REJECTED = "rejected"
    PAID = "paid"


class ExceptionReason(str, Enum):
    PRICE_VARIANCE = "price_variance"
    QUANTITY_VARIANCE = "quantity_variance"
    TOTAL_MISMATCH = "total_mismatch"
    MISSING_APPROVAL = "missing_approval"
    OFF_CONTRACT_TERM = "off_contract_term"
    UNKNOWN_VENDOR = "unknown_vendor"
    PO_NOT_FOUND = "po_not_found"
    CONTRACT_NOT_FOUND = "contract_not_found"
    EXCEEDS_THRESHOLD = "exceeds_threshold"
    MALFORMED_EXTRACTION = "malformed_extraction"


class AuditEventType(str, Enum):
    EXTRACTED = "extracted"
    VALIDATED = "validated"
    MATCHED = "matched"
    EXCEPTION_RAISED = "exception_raised"
    REJECTED = "rejected"
    APPROVED = "approved"
    PAID = "paid"
    HUMAN_REVIEW = "human_review"


# ---------------------------------------------------------------------------
# Core financial models
# ---------------------------------------------------------------------------


class LineItem(BaseModel):
    """A single line on an invoice or PO."""

    line_number: int = Field(..., ge=1)
    description: str = Field(..., min_length=1)
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., gt=0)
    total: Decimal = Field(..., gt=0)

    @model_validator(mode="after")
    def check_line_total(self) -> "LineItem":
        expected = (self.quantity * self.unit_price).quantize(Decimal("0.01"))
        actual = self.total.quantize(Decimal("0.01"))
        if abs(expected - actual) > Decimal("0.02"):  # 2-cent tolerance
            raise ValueError(
                f"Line {self.line_number}: total {actual} does not match "
                f"qty×price = {expected}"
            )
        return self


class Invoice(BaseModel):
    """Extracted and validated invoice payload."""

    invoice_id: UUID = Field(default_factory=uuid4)
    invoice_number: str = Field(..., min_length=1)
    vendor_id: str = Field(..., min_length=1)
    vendor_name: str = Field(..., min_length=1)
    po_number: str = Field(..., min_length=1)
    invoice_date: date
    due_date: Optional[date] = None
    line_items: list[LineItem] = Field(..., min_length=1)
    subtotal: Decimal = Field(..., gt=0)
    tax: Decimal = Field(default=Decimal("0.00"), ge=0)
    total: Decimal = Field(..., gt=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    status: InvoiceStatus = InvoiceStatus.PENDING
    exception_reasons: list[ExceptionReason] = Field(default_factory=list)
    raw_text: Optional[str] = None  # original text kept for audit

    @model_validator(mode="after")
    def check_invoice_total(self) -> "Invoice":
        computed = sum(item.total for item in self.line_items)
        computed_total = (computed + self.tax).quantize(Decimal("0.01"))
        stated_total = self.total.quantize(Decimal("0.01"))
        if abs(computed_total - stated_total) > Decimal("0.05"):
            raise ValueError(
                f"Invoice total {stated_total} does not match "
                f"line-items+tax = {computed_total}"
            )
        return self


# ---------------------------------------------------------------------------
# Reference data models (PO & Contract)
# ---------------------------------------------------------------------------


class POLineItem(BaseModel):
    """A single line on a purchase order."""

    line_number: int = Field(..., ge=1)
    description: str
    quantity_ordered: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., gt=0)


class PurchaseOrder(BaseModel):
    """Purchase order against which invoices are matched."""

    po_number: str = Field(..., min_length=1)
    vendor_id: str = Field(..., min_length=1)
    vendor_name: str
    issue_date: date
    expiry_date: Optional[date] = None
    line_items: list[POLineItem] = Field(..., min_length=1)
    total_value: Decimal = Field(..., gt=0)
    approved_by: Optional[str] = None  # None → missing approval
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")


class ContractTerm(BaseModel):
    """A single negotiated term in a vendor contract."""

    item_code: str
    description: str
    agreed_unit_price: Decimal = Field(..., gt=0)
    price_tolerance_pct: Decimal = Field(
        default=Decimal("0.02"), ge=0, le=1
    )  # e.g. 2 % tolerance


class Contract(BaseModel):
    """Vendor contract defining agreed prices and thresholds."""

    contract_id: str = Field(..., min_length=1)
    vendor_id: str = Field(..., min_length=1)
    vendor_name: str
    effective_date: date
    expiry_date: Optional[date] = None
    terms: list[ContractTerm] = Field(default_factory=list)
    payment_terms_days: int = Field(default=30, ge=0)  # net-30 etc.
    auto_approval_threshold: Decimal = Field(
        default=Decimal("10000.00"), gt=0
    )  # invoices above this require human sign-off
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")

    @field_validator("expiry_date")
    @classmethod
    def expiry_after_effective(
        cls, v: Optional[date], info  # noqa: ANN001
    ) -> Optional[date]:
        if v is not None and "effective_date" in info.data:
            if v < info.data["effective_date"]:
                raise ValueError("expiry_date must be after effective_date")
        return v


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


class AuditTrailEntry(BaseModel):
    """Immutable record of a single event in the invoice lifecycle."""

    entry_id: UUID = Field(default_factory=uuid4)
    invoice_id: UUID
    invoice_number: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    event_type: AuditEventType
    actor: str = Field(default="system")  # "system" or human user-id
    details: dict = Field(default_factory=dict)
    previous_status: Optional[InvoiceStatus] = None
    new_status: Optional[InvoiceStatus] = None
