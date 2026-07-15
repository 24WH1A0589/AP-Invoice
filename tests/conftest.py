"""
Shared pytest fixtures for AP Invoice & Contract Exception Agent tests.

Provides:
  - po_registry      : dict[str, PurchaseOrder]  — PO-5551 (clean) + PO-5552 (high-value)
  - contract_registry: dict[str, Contract]        — acme_corp ($38/unit, 2% tol, $10k threshold)
  - matcher          : MatchingEngine
  - router           : ExceptionRouter
  - make_invoice     : factory helper → Invoice
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ap_agent.matching import MatchingEngine
from ap_agent.router import ExceptionRouter
from ap_agent.schemas import (
    Contract,
    ContractTerm,
    Invoice,
    InvoiceStatus,
    LineItem,
    POLineItem,
    PurchaseOrder,
)


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def po_registry() -> dict[str, PurchaseOrder]:
    """
    PO-5551  — acme_corp, 120 × Widget @ $38.00, approved, total $4 560
    PO-5552  — acme_corp, high-value, NO approver, total $15 000
    """
    return {
        "PO-5551": PurchaseOrder(
            po_number="PO-5551",
            vendor_id="acme_corp",
            vendor_name="Acme Corporation",
            issue_date=date(2024, 1, 1),
            expiry_date=date(2026, 12, 31),
            line_items=[
                POLineItem(
                    line_number=1,
                    description="Widget",
                    quantity_ordered=Decimal("120"),
                    unit_price=Decimal("38.00"),
                ),
            ],
            total_value=Decimal("4560.00"),
            approved_by="controller@company.com",
        ),
        "PO-5552": PurchaseOrder(
            po_number="PO-5552",
            vendor_id="acme_corp",
            vendor_name="Acme Corporation",
            issue_date=date(2024, 1, 1),
            expiry_date=date(2026, 12, 31),
            line_items=[
                POLineItem(
                    line_number=1,
                    description="Widget",
                    quantity_ordered=Decimal("400"),
                    unit_price=Decimal("38.00"),
                ),
            ],
            total_value=Decimal("15200.00"),
            approved_by=None,          # ← no approval on file
        ),
    }


@pytest.fixture(scope="session")
def contract_registry() -> dict[str, Contract]:
    """acme_corp contract: Widget @ $38.00 ±2 %, auto-approval threshold $10 000."""
    return {
        "acme_corp": Contract(
            contract_id="CTR-ACME-TEST",
            vendor_id="acme_corp",
            vendor_name="Acme Corporation",
            effective_date=date(2024, 1, 1),
            expiry_date=date(2026, 12, 31),
            terms=[
                ContractTerm(
                    item_code="WGT",
                    description="Widget",
                    agreed_unit_price=Decimal("38.00"),
                    price_tolerance_pct=Decimal("0.02"),   # 2 %
                ),
            ],
            payment_terms_days=30,
            auto_approval_threshold=Decimal("10000.00"),
        ),
    }


@pytest.fixture(scope="session")
def matcher(po_registry, contract_registry) -> MatchingEngine:
    return MatchingEngine(po_registry, contract_registry)


@pytest.fixture(scope="session")
def router() -> ExceptionRouter:
    return ExceptionRouter()


# ---------------------------------------------------------------------------
# Invoice factory
# ---------------------------------------------------------------------------

def _make_invoice(
    *,
    invoice_number: str = "INV-TEST-001",
    vendor_id: str = "acme_corp",
    vendor_name: str = "Acme Corporation",
    po_number: str = "PO-5551",
    invoice_date: date = date(2024, 6, 15),
    quantity: Decimal = Decimal("120"),
    unit_price: Decimal = Decimal("38.00"),
    total_override: Decimal | None = None,
    status: InvoiceStatus = InvoiceStatus.PENDING,
    raw_text: str = "",
) -> Invoice:
    line_total = (quantity * unit_price).quantize(Decimal("0.01"))
    inv_total = total_override if total_override is not None else line_total

    return Invoice(
        invoice_number=invoice_number,
        vendor_id=vendor_id,
        vendor_name=vendor_name,
        po_number=po_number,
        invoice_date=invoice_date,
        line_items=[
            LineItem(
                line_number=1,
                description="Widget",
                quantity=quantity,
                unit_price=unit_price,
                total=line_total,
            )
        ],
        subtotal=line_total,
        tax=Decimal("0.00"),
        total=inv_total,
        currency="USD",
        status=status,
        raw_text=raw_text,
    )


@pytest.fixture
def make_invoice():
    """Return the _make_invoice factory so tests can call it with keyword args."""
    return _make_invoice
