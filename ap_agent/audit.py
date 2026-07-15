"""
Audit trail persistence layer.

Appends immutable AuditTrailEntry records to a JSONL file
(one JSON object per line) so every invoice event is queryable offline.

Usage
-----
    ledger = AuditLedger("audit_trail.jsonl")
    ledger.record(AuditTrailEntry(...))
    entries = ledger.query(invoice_id=some_uuid)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from uuid import UUID

from .schemas import (
    AuditEventType,
    AuditTrailEntry,
    Invoice,
    InvoiceStatus,
)


class AuditLedger:
    """
    Append-only JSONL audit ledger.

    Parameters
    ----------
    path : str | Path
        File path for the JSONL audit log.  Created automatically.
    """

    def __init__(self, path: str | Path = "audit_trail.jsonl") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Touch the file if it doesn't exist
        if not self._path.exists():
            self._path.touch()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record(self, entry: AuditTrailEntry) -> None:
        """Append a single audit entry to the ledger (thread-safe via file append)."""
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(entry.model_dump_json() + "\n")

    # ------------------------------------------------------------------
    # Convenience factory methods
    # ------------------------------------------------------------------

    def log_extracted(self, invoice: Invoice) -> AuditTrailEntry:
        entry = AuditTrailEntry(
            invoice_id=invoice.invoice_id,
            invoice_number=invoice.invoice_number,
            event_type=AuditEventType.EXTRACTED,
            details={
                "vendor_id": invoice.vendor_id,
                "vendor_name": invoice.vendor_name,
                "po_number": invoice.po_number,
                "invoice_date": str(invoice.invoice_date),
                "total": str(invoice.total),
                "currency": invoice.currency,
                "line_count": len(invoice.line_items),
            },
            previous_status=None,
            new_status=invoice.status,
        )
        self.record(entry)
        return entry

    def log_validated(self, invoice: Invoice) -> AuditTrailEntry:
        entry = AuditTrailEntry(
            invoice_id=invoice.invoice_id,
            invoice_number=invoice.invoice_number,
            event_type=AuditEventType.VALIDATED,
            details={"status": invoice.status.value},
            previous_status=InvoiceStatus.PENDING,
            new_status=invoice.status,
        )
        self.record(entry)
        return entry

    def log_matched(
        self, invoice: Invoice, match_details: dict
    ) -> AuditTrailEntry:
        entry = AuditTrailEntry(
            invoice_id=invoice.invoice_id,
            invoice_number=invoice.invoice_number,
            event_type=AuditEventType.MATCHED,
            details=match_details,
            previous_status=InvoiceStatus.PENDING,
            new_status=InvoiceStatus.MATCHED,
        )
        self.record(entry)
        return entry

    def log_exception(
        self,
        invoice: Invoice,
        exceptions: list,
        details: dict,
        priority: int,
    ) -> AuditTrailEntry:
        entry = AuditTrailEntry(
            invoice_id=invoice.invoice_id,
            invoice_number=invoice.invoice_number,
            event_type=AuditEventType.EXCEPTION_RAISED,
            details={
                "exceptions": [e.value for e in exceptions],
                "priority": priority,
                **details,
            },
            previous_status=InvoiceStatus.PENDING,
            new_status=InvoiceStatus.EXCEPTION,
        )
        self.record(entry)
        return entry

    def log_rejected(self, invoice: Invoice, reason: str) -> AuditTrailEntry:
        entry = AuditTrailEntry(
            invoice_id=invoice.invoice_id,
            invoice_number=invoice.invoice_number,
            event_type=AuditEventType.REJECTED,
            details={"reason": reason, "raw_snippet": (invoice.raw_text or "")[:300]},
            previous_status=InvoiceStatus.PENDING,
            new_status=InvoiceStatus.REJECTED,
        )
        self.record(entry)
        return entry

    def log_paid(self, invoice: Invoice, actor: str = "system") -> AuditTrailEntry:
        entry = AuditTrailEntry(
            invoice_id=invoice.invoice_id,
            invoice_number=invoice.invoice_number,
            event_type=AuditEventType.PAID,
            actor=actor,
            details={"total": str(invoice.total), "currency": invoice.currency},
            previous_status=InvoiceStatus.MATCHED,
            new_status=InvoiceStatus.PAID,
        )
        self.record(entry)
        return entry

    def log_human_review(
        self,
        invoice: Invoice,
        reviewer_notes: str,
        actor: str = "system",
    ) -> AuditTrailEntry:
        entry = AuditTrailEntry(
            invoice_id=invoice.invoice_id,
            invoice_number=invoice.invoice_number,
            event_type=AuditEventType.HUMAN_REVIEW,
            actor=actor,
            details={"reviewer_notes": reviewer_notes},
            previous_status=InvoiceStatus.EXCEPTION,
            new_status=InvoiceStatus.EXCEPTION,
        )
        self.record(entry)
        return entry

    # ------------------------------------------------------------------
    # Read / query
    # ------------------------------------------------------------------

    def all_entries(self) -> list[AuditTrailEntry]:
        """Load all entries from the JSONL file."""
        entries: list[AuditTrailEntry] = []
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(AuditTrailEntry(**json.loads(line)))
        return entries

    def query(
        self,
        invoice_id: Optional[UUID] = None,
        invoice_number: Optional[str] = None,
        event_type: Optional[AuditEventType] = None,
    ) -> list[AuditTrailEntry]:
        """Filter entries by optional criteria."""
        results = []
        for entry in self.all_entries():
            if invoice_id and entry.invoice_id != invoice_id:
                continue
            if invoice_number and entry.invoice_number != invoice_number:
                continue
            if event_type and entry.event_type != event_type:
                continue
            results.append(entry)
        return results
