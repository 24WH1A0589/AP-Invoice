"""
demo.py — end-to-end demonstration of the AP Invoice & Contract Exception Agent.

Scenarios
---------
1. Happy path          — clean invoice, matches PO + contract exactly → STP PAID
2. Price variance      — unit price exceeds contract tolerance → HUMAN REVIEW
3. Quantity mismatch   — quantity on invoice ≠ PO quantity ordered → HUMAN REVIEW
4. Unknown vendor      — invoice vendor_id does not match PO vendor → HUMAN REVIEW
5. Malformed invoice   — required fields missing → REJECTED

Run
---
    python demo.py

Requires OPENROUTER_API_KEY in environment (or a .env file at project root).
Get your free key at: https://openrouter.ai/keys
"""

from __future__ import annotations

import os
from decimal import Decimal
from datetime import date

from dotenv import load_dotenv

load_dotenv()  # pick up .env if present

from ap_agent.orchestrator import APInvoiceOrchestrator
from ap_agent.schemas import (
    Contract,
    ContractTerm,
    POLineItem,
    PurchaseOrder,
)

# ---------------------------------------------------------------------------
# Reference data  (PO registry + contract registry)
# ---------------------------------------------------------------------------

PO_REGISTRY: dict[str, PurchaseOrder] = {
    "PO-2024-001": PurchaseOrder(
        po_number="PO-2024-001",
        vendor_id="acme_corp",
        vendor_name="Acme Corporation",
        issue_date=date(2024, 1, 10),
        expiry_date=date(2025, 12, 31),
        line_items=[
            POLineItem(
                line_number=1,
                description="Widget Type A",
                quantity_ordered=Decimal("100"),
                unit_price=Decimal("25.00"),
            ),
            POLineItem(
                line_number=2,
                description="Widget Type B",
                quantity_ordered=Decimal("50"),
                unit_price=Decimal("40.00"),
            ),
        ],
        total_value=Decimal("4500.00"),
        approved_by="jane.doe@company.com",
    ),
    "PO-2024-002": PurchaseOrder(
        po_number="PO-2024-002",
        vendor_id="globex_ltd",
        vendor_name="Globex Ltd",
        issue_date=date(2024, 3, 1),
        expiry_date=date(2025, 12, 31),
        line_items=[
            POLineItem(
                line_number=1,
                description="Server Rack Unit",
                quantity_ordered=Decimal("5"),
                unit_price=Decimal("1200.00"),
            ),
        ],
        total_value=Decimal("6000.00"),
        approved_by="john.smith@company.com",
    ),
    "PO-2024-003": PurchaseOrder(
        po_number="PO-2024-003",
        vendor_id="techsupply_inc",
        vendor_name="TechSupply Inc",
        issue_date=date(2024, 2, 15),
        expiry_date=date(2025, 12, 31),
        line_items=[
            POLineItem(
                line_number=1,
                description="Laptop Stand",
                quantity_ordered=Decimal("20"),
                unit_price=Decimal("35.00"),
            ),
        ],
        total_value=Decimal("700.00"),
        approved_by="alice.jones@company.com",
    ),
}

CONTRACT_REGISTRY: dict[str, Contract] = {
    "acme_corp": Contract(
        contract_id="CTR-ACME-2024",
        vendor_id="acme_corp",
        vendor_name="Acme Corporation",
        effective_date=date(2024, 1, 1),
        expiry_date=date(2025, 12, 31),
        terms=[
            ContractTerm(
                item_code="WA",
                description="Widget Type A",
                agreed_unit_price=Decimal("25.00"),
                price_tolerance_pct=Decimal("0.02"),  # 2 %
            ),
            ContractTerm(
                item_code="WB",
                description="Widget Type B",
                agreed_unit_price=Decimal("40.00"),
                price_tolerance_pct=Decimal("0.02"),
            ),
        ],
        payment_terms_days=30,
        auto_approval_threshold=Decimal("10000.00"),
    ),
    "globex_ltd": Contract(
        contract_id="CTR-GLOBEX-2024",
        vendor_id="globex_ltd",
        vendor_name="Globex Ltd",
        effective_date=date(2024, 3, 1),
        expiry_date=date(2025, 12, 31),
        terms=[
            ContractTerm(
                item_code="SRU",
                description="Server Rack Unit",
                agreed_unit_price=Decimal("1200.00"),
                price_tolerance_pct=Decimal("0.01"),  # 1 %
            ),
        ],
        payment_terms_days=45,
        auto_approval_threshold=Decimal("10000.00"),
    ),
}

# ---------------------------------------------------------------------------
# Sample invoice texts
# ---------------------------------------------------------------------------

# Scenario 1 — Happy path: exact match on price, qty, total
INVOICE_1_HAPPY = """
INVOICE

Invoice Number: INV-5001
Vendor: Acme Corporation
Vendor ID: acme_corp
Purchase Order: PO-2024-001
Invoice Date: 2024-06-15
Due Date: 2024-07-15

Line Items:
  1. Widget Type A     Qty: 100    Unit Price: $25.00    Total: $2,500.00
  2. Widget Type B     Qty: 50     Unit Price: $40.00    Total: $2,000.00

Subtotal: $4,500.00
Tax: $0.00
Total Due: $4,500.00
Currency: USD
"""

# Scenario 2 — Price variance: Widget Type A invoiced at $27.00 (>2% above $25)
INVOICE_2_PRICE_VARIANCE = """
INVOICE

Invoice Number: INV-5002
Vendor: Acme Corporation
Vendor ID: acme_corp
Purchase Order: PO-2024-001
Invoice Date: 2024-07-01
Due Date: 2024-07-31

Line Items:
  1. Widget Type A     Qty: 100    Unit Price: $27.00    Total: $2,700.00
  2. Widget Type B     Qty: 50     Unit Price: $40.00    Total: $2,000.00

Subtotal: $4,700.00
Tax: $0.00
Total Due: $4,700.00
Currency: USD
"""

# Scenario 3 — Quantity mismatch: 10 Server Rack Units invoiced, PO has 5
INVOICE_3_QTY_MISMATCH = """
INVOICE

Invoice Number: INV-5003
Vendor: Globex Ltd
Vendor ID: globex_ltd
Purchase Order: PO-2024-002
Invoice Date: 2024-07-05
Due Date: 2024-08-05

Line Items:
  1. Server Rack Unit  Qty: 10     Unit Price: $1,200.00  Total: $12,000.00

Subtotal: $12,000.00
Tax: $0.00
Total Due: $12,000.00
Currency: USD
"""

# Scenario 4 — Wrong vendor: invoice says vendor_id "rogue_vendor"
INVOICE_4_UNKNOWN_VENDOR = """
INVOICE

Invoice Number: INV-5004
Vendor: Rogue Supplier Co
Vendor ID: rogue_vendor
Purchase Order: PO-2024-003
Invoice Date: 2024-07-10
Due Date: 2024-08-10

Line Items:
  1. Laptop Stand      Qty: 20     Unit Price: $35.00    Total: $700.00

Subtotal: $700.00
Tax: $0.00
Total Due: $700.00
Currency: USD
"""

# Scenario 5 — Malformed: missing invoice number and line items
INVOICE_5_MALFORMED = """
This is some text that is supposed to be an invoice but is missing
almost all required fields. No vendor, no PO, no amounts.
Just garbage text from a scanned document that failed OCR badly.
"""

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    agent = APInvoiceOrchestrator(
        po_registry=PO_REGISTRY,
        contract_registry=CONTRACT_REGISTRY,
        audit_path="audit_trail.jsonl",
    )

    invoices = [
        INVOICE_1_HAPPY,
        INVOICE_2_PRICE_VARIANCE,
        INVOICE_3_QTY_MISMATCH,
        INVOICE_4_UNKNOWN_VENDOR,
        INVOICE_5_MALFORMED,
    ]

    results = agent.process_batch(invoices)

    # Print audit trail summary
    from ap_agent.audit import AuditLedger
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()
    ledger = AuditLedger("audit_trail.jsonl")
    entries = ledger.all_entries()

    table = Table(title="Audit Trail", box=box.MINIMAL_DOUBLE_HEAD)
    table.add_column("Timestamp", style="dim", width=26)
    table.add_column("Invoice #", style="cyan")
    table.add_column("Event", style="magenta")
    table.add_column("Status change")

    for e in entries:
        prev = e.previous_status.value if e.previous_status else "—"
        nxt = e.new_status.value if e.new_status else "—"
        table.add_row(
            str(e.timestamp)[:19],
            e.invoice_number,
            e.event_type.value,
            f"{prev} -> {nxt}",
        )

    console.print()
    console.print(table)


if __name__ == "__main__":
    main()
