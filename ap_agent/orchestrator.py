"""
Main orchestrator — ties extraction → matching → routing → audit together.

Flow per invoice
----------------
1. ExtractionAgent parses raw text → Invoice (or stub REJECTED invoice on failure)
2. AuditLedger.log_extracted()
3. MatchingEngine.match() → MatchResult
4. ExceptionRouter.route() → RouteDecision
5. Update invoice status + log final audit event
6. Return ProcessingResult (carries everything the caller needs)

The orchestrator is intentionally thin: each component owns its own logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from .audit import AuditLedger
from .extraction import ExtractionAgent
from .matching import MatchingEngine, MatchResult
from .router import ExceptionRouter, RouteDecision, RoutingAction
from .schemas import (
    Contract,
    Invoice,
    InvoiceStatus,
    PurchaseOrder,
)

console = Console()


@dataclass
class ProcessingResult:
    """Everything the caller needs after processing one invoice."""

    invoice: Invoice
    match_result: Optional[MatchResult]
    decision: RouteDecision


class APInvoiceOrchestrator:
    """
    Top-level agent.

    Parameters
    ----------
    po_registry : dict[str, PurchaseOrder]
    contract_registry : dict[str, Contract]
    audit_path : str | Path
        Where to write the JSONL audit log.
    model : str
        OpenAI model for extraction.
    """

    def __init__(
        self,
        po_registry: dict[str, PurchaseOrder],
        contract_registry: dict[str, Contract],
        audit_path: str | Path = "audit_trail.jsonl",
        model: str = "nousresearch/hermes-3-llama-3.1-405b:free",
    ) -> None:
        self._extractor = ExtractionAgent(model=model)
        self._matcher = MatchingEngine(po_registry, contract_registry)
        self._router = ExceptionRouter()
        self._ledger = AuditLedger(audit_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(self, raw_text: str) -> ProcessingResult:
        """
        Process a single raw invoice string end-to-end.
        Returns a ProcessingResult regardless of outcome.
        """
        # ── Step 1: Extract ──────────────────────────────────────────
        invoice: Invoice
        match_result: Optional[MatchResult] = None

        try:
            invoice = self._extractor.extract(raw_text)
        except (ValidationError, ValueError) as exc:
            invoice = ExtractionAgent.make_rejected_invoice(raw_text, exc)
            self._ledger.log_rejected(invoice, str(exc))
            decision = self._router.route(invoice, _empty_match(invoice))
            self._print_result(decision, invoice)
            return ProcessingResult(invoice=invoice, match_result=None, decision=decision)

        # ── Step 2: Audit extraction ──────────────────────────────────
        self._ledger.log_extracted(invoice)

        # ── Step 3: Match ─────────────────────────────────────────────
        match_result = self._matcher.match(invoice)

        # ── Step 4: Route ─────────────────────────────────────────────
        decision = self._router.route(invoice, match_result)

        # ── Step 5: Update status & persist ───────────────────────────
        if decision.action == RoutingAction.STRAIGHT_THROUGH_PAY:
            invoice.status = InvoiceStatus.PAID
            self._ledger.log_matched(invoice, match_result.details)
            self._ledger.log_paid(invoice)

        elif decision.action == RoutingAction.HUMAN_REVIEW:
            invoice.status = InvoiceStatus.EXCEPTION
            self._ledger.log_exception(
                invoice,
                match_result.exceptions,
                match_result.details,
                decision.priority,
            )
            self._ledger.log_human_review(invoice, decision.reviewer_notes)

        elif decision.action == RoutingAction.REJECT:
            invoice.status = InvoiceStatus.REJECTED
            self._ledger.log_rejected(invoice, decision.summary)

        # ── Step 6: Print rich output ─────────────────────────────────
        self._print_result(decision, invoice)

        return ProcessingResult(
            invoice=invoice,
            match_result=match_result,
            decision=decision,
        )

    def process_batch(self, raw_texts: list[str]) -> list[ProcessingResult]:
        """Process multiple invoices and print a summary table."""
        results = []
        for i, text in enumerate(raw_texts, 1):
            console.rule(f"[bold]Invoice {i}/{len(raw_texts)}")
            results.append(self.process(text))

        self._print_summary(results)
        return results

    # ------------------------------------------------------------------
    # Private display helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _action_style(action: RoutingAction) -> str:
        return {
            RoutingAction.STRAIGHT_THROUGH_PAY: "bold green",
            RoutingAction.HUMAN_REVIEW: "bold yellow",
            RoutingAction.REJECT: "bold red",
        }.get(action, "white")

    def _print_result(self, decision: RouteDecision, invoice: Invoice) -> None:
        style = self._action_style(decision.action)
        action_label = decision.action.value.replace("_", " ").upper()

        content_lines = [
            f"[bold]Invoice:[/bold]  {decision.invoice_number}",
            f"[bold]Vendor:[/bold]   {invoice.vendor_name}",
            f"[bold]Total:[/bold]    {invoice.currency} {invoice.total:.2f}",
            f"[bold]Action:[/bold]   [{style}]{action_label}[/{style}]",
        ]

        if decision.exceptions:
            exc_list = ", ".join(e.value for e in decision.exceptions)
            content_lines.append(f"[bold]Exceptions:[/bold] {exc_list}")

        if decision.reviewer_notes:
            content_lines.append("")
            content_lines.append("[bold]Reviewer notes:[/bold]")
            for line in decision.reviewer_notes.splitlines():
                content_lines.append(f"  {line}")

        console.print(
            Panel(
                "\n".join(content_lines),
                title=f"[{style}]{action_label}[/{style}]",
                border_style=style,
                expand=False,
            )
        )

    @staticmethod
    def _print_summary(results: list[ProcessingResult]) -> None:
        table = Table(title="Batch Processing Summary", box=box.SIMPLE_HEAVY)
        table.add_column("Invoice #", style="cyan")
        table.add_column("Vendor", style="white")
        table.add_column("Total", justify="right")
        table.add_column("Action", justify="center")
        table.add_column("Exceptions", style="yellow")

        for r in results:
            inv = r.invoice
            d = r.decision
            action_style = {
                RoutingAction.STRAIGHT_THROUGH_PAY: "green",
                RoutingAction.HUMAN_REVIEW: "yellow",
                RoutingAction.REJECT: "red",
            }.get(d.action, "white")
            table.add_row(
                inv.invoice_number,
                inv.vendor_name,
                f"{inv.currency} {inv.total:.2f}",
                f"[{action_style}]{d.action.value}[/{action_style}]",
                ", ".join(e.value for e in d.exceptions) or "—",
            )

        console.print()
        console.print(table)

        paid = sum(
            1 for r in results if r.decision.action == RoutingAction.STRAIGHT_THROUGH_PAY
        )
        reviewed = sum(
            1 for r in results if r.decision.action == RoutingAction.HUMAN_REVIEW
        )
        rejected = sum(
            1 for r in results if r.decision.action == RoutingAction.REJECT
        )
        total = len(results)
        stp_rate = (paid / total * 100) if total else 0

        console.print(
            f"[bold]STP rate:[/bold] {paid}/{total} ({stp_rate:.0f}%)  "
            f"| [yellow]Review: {reviewed}[/yellow]  "
            f"| [red]Rejected: {rejected}[/red]"
        )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _empty_match(invoice: Invoice) -> MatchResult:
    """Create a trivial MatchResult for already-rejected invoices."""
    from .matching import MatchResult  # local import avoids circular

    return MatchResult(
        invoice_number=invoice.invoice_number,
        status=InvoiceStatus.REJECTED,
        exceptions=invoice.exception_reasons,
        details={},
    )
