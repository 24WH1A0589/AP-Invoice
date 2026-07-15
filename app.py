"""
app.py — AP Autopilot  (Streamlit UI matching the finflow screenshot)

Layout
------
  Header   — dark navy bar: title + subtitle left, STP% + exception count right
  Card     — per-invoice: field-comparison table + exception banner + action button
  Strip    — two-column footer: last STP invoice (left) | audit log entry (right)
"""
from __future__ import annotations

import time
from datetime import date
from decimal import Decimal

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from ap_agent.orchestrator import APInvoiceOrchestrator, ProcessingResult
from ap_agent.router import RoutingAction
from ap_agent.schemas import (
    Contract, ContractTerm, ExceptionReason,
    POLineItem, PurchaseOrder,
)
from ap_agent.audit import AuditLedger

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AP Autopilot",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── registries ────────────────────────────────────────────────────────────────
PO_REGISTRY: dict[str, PurchaseOrder] = {
    "PO-2024-001": PurchaseOrder(
        po_number="PO-2024-001", vendor_id="acme_corp",
        vendor_name="Acme Corporation",
        issue_date=date(2024, 1, 10), expiry_date=date(2025, 12, 31),
        line_items=[
            POLineItem(line_number=1, description="Widget Type A",
                       quantity_ordered=Decimal("100"), unit_price=Decimal("25.00")),
            POLineItem(line_number=2, description="Widget Type B",
                       quantity_ordered=Decimal("50"),  unit_price=Decimal("40.00")),
        ],
        total_value=Decimal("4500.00"), approved_by="jane.doe@company.com",
    ),
    "PO-2024-002": PurchaseOrder(
        po_number="PO-2024-002", vendor_id="globex_ltd",
        vendor_name="Globex Ltd",
        issue_date=date(2024, 3, 1), expiry_date=date(2025, 12, 31),
        line_items=[
            POLineItem(line_number=1, description="Server Rack Unit",
                       quantity_ordered=Decimal("5"), unit_price=Decimal("1200.00")),
        ],
        total_value=Decimal("6000.00"), approved_by="john.smith@company.com",
    ),
    "PO-2024-003": PurchaseOrder(
        po_number="PO-2024-003", vendor_id="techsupply_inc",
        vendor_name="TechSupply Inc",
        issue_date=date(2024, 2, 15), expiry_date=date(2025, 12, 31),
        line_items=[
            POLineItem(line_number=1, description="Laptop Stand",
                       quantity_ordered=Decimal("20"), unit_price=Decimal("35.00")),
        ],
        total_value=Decimal("700.00"), approved_by="alice.jones@company.com",
    ),
}

CONTRACT_REGISTRY: dict[str, Contract] = {
    "acme_corp": Contract(
        contract_id="CTR-ACME-2024", vendor_id="acme_corp",
        vendor_name="Acme Corporation",
        effective_date=date(2024, 1, 1), expiry_date=date(2025, 12, 31),
        terms=[
            ContractTerm(item_code="WA", description="Widget Type A",
                         agreed_unit_price=Decimal("25.00"),
                         price_tolerance_pct=Decimal("0.02")),
            ContractTerm(item_code="WB", description="Widget Type B",
                         agreed_unit_price=Decimal("40.00"),
                         price_tolerance_pct=Decimal("0.02")),
        ],
        payment_terms_days=30, auto_approval_threshold=Decimal("10000.00"),
    ),
    "globex_ltd": Contract(
        contract_id="CTR-GLOBEX-2024", vendor_id="globex_ltd",
        vendor_name="Globex Ltd",
        effective_date=date(2024, 3, 1), expiry_date=date(2025, 12, 31),
        terms=[
            ContractTerm(item_code="SRU", description="Server Rack Unit",
                         agreed_unit_price=Decimal("1200.00"),
                         price_tolerance_pct=Decimal("0.01")),
        ],
        payment_terms_days=45, auto_approval_threshold=Decimal("10000.00"),
    ),
}

AUDIT_PATH = "audit_trail.jsonl"

# ── session state ─────────────────────────────────────────────────────────────
if "results"         not in st.session_state:
    st.session_state.results: list[ProcessingResult] = []
if "run_duration"    not in st.session_state:
    st.session_state.run_duration: float = 0.0
if "total_processed" not in st.session_state:
    st.session_state.total_processed: int = 0

# ── helpers ───────────────────────────────────────────────────────────────────

def get_agent() -> APInvoiceOrchestrator:
    if "agent" not in st.session_state:
        st.session_state.agent = APInvoiceOrchestrator(
            po_registry=PO_REGISTRY,
            contract_registry=CONTRACT_REGISTRY,
            audit_path=AUDIT_PATH,
        )
    return st.session_state.agent


def compute_stats(results: list[ProcessingResult]) -> dict:
    total    = len(results)
    paid     = sum(1 for r in results if r.decision.action == RoutingAction.STRAIGHT_THROUGH_PAY)
    review   = sum(1 for r in results if r.decision.action == RoutingAction.HUMAN_REVIEW)
    rejected = sum(1 for r in results if r.decision.action == RoutingAction.REJECT)
    stp      = (paid / total * 100) if total else 0
    return dict(total=total, paid=paid, review=review, rejected=rejected, stp=stp)


def last_stp_result(results: list[ProcessingResult]) -> ProcessingResult | None:
    for r in reversed(results):
        if r.decision.action == RoutingAction.STRAIGHT_THROUGH_PAY:
            return r
    return None

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="collapsedControl"] { display: none; }
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding-top: 0 !important; max-width: 100% !important; }

/* header bar */
.ap-header {
    background: #0f1e2e; color: #fff;
    padding: 18px 36px; display: flex;
    align-items: center; justify-content: space-between;
    margin-bottom: 32px;
}
.ap-header h1 { margin: 0; font-size: 1.5rem; font-weight: 700; color: #fff; }
.ap-header p  { margin: 3px 0 0; font-size: 0.78rem; color: #8eacc4; }
.ap-kpi       { text-align: right; }
.ap-kpi-stp   { font-size: 1rem; font-weight: 700; color: #f5c842; }
.ap-kpi-exc   { font-size: 0.82rem; color: #f5c842; margin-top: 2px; }

/* card wrapper */
.ap-card {
    background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
    padding: 22px 26px 16px; margin-bottom: 24px;
    box-shadow: 0 1px 4px rgba(0,0,0,.06);
}
.ap-card-heading {
    display: flex; align-items: center; gap: 10px;
    font-size: 1rem; font-weight: 700; color: #1a202c; margin-bottom: 3px;
}
.ap-card-sub { font-size: 0.78rem; color: #718096; margin-bottom: 14px; }

/* badges */
.badge {
    display: inline-block; font-size: 0.67rem; font-weight: 700;
    letter-spacing: .07em; padding: 3px 9px; border-radius: 4px;
    text-transform: uppercase; white-space: nowrap;
}
.badge-exc  { background:#fff0f0; color:#c0392b; border:1px solid #f5c6c6; }
.badge-route{ background:#fef6dc; color:#7d5500; border:1px solid #f9de8a; }
.badge-stp  { background:#e8faf2; color:#1a7a4a; border:1px solid #9ee5c0; }
.badge-rej  { background:#fff0f0; color:#c0392b; border:1px solid #f5c6c6; }

/* comparison table */
.cmp { width:100%; border-collapse:collapse; font-size:0.83rem; }
.cmp th {
    background:#0f1e2e; color:#fff; text-align:left;
    padding:9px 14px; font-size:0.78rem; font-weight:600;
}
.cmp td { padding:9px 14px; border-bottom:1px solid #f0f4f8; color:#2d3748; }
.cmp tr:last-child td { border-bottom: none; }
.cmp tr.bad td { background: #fff8f8; }
.ok  { color:#27ae60; font-weight:700; }
.bad { color:#e74c3c; font-weight:700; }
.wrn { color:#e67e22; font-weight:700; }

/* exception banner */
.exc-banner {
    background:#fffbea; border:1px solid #f6e05e; border-radius:6px;
    padding:12px 16px; margin-top:14px; font-size:0.82rem; color:#5a4600;
    display:flex; align-items:center; justify-content:space-between; gap:16px;
}

/* bottom strip */
.strip { display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-top:8px; }
.sc {
    background:#fff; border:1px solid #e2e8f0; border-radius:8px;
    padding:16px 20px; font-size:0.82rem;
}
.sc-title {
    font-size:0.82rem; font-weight:700; color:#1a202c;
    display:flex; align-items:center; gap:8px; margin-bottom:4px;
}
.sc-body { color:#718096; font-size:0.78rem; line-height:1.55; }
</style>
""", unsafe_allow_html=True)


# ── render_header ─────────────────────────────────────────────────────────────

def render_header(results: list[ProcessingResult]) -> None:
    """Dark navy top bar: branding left, live KPIs right."""
    stats = compute_stats(results)
    stp_str = f"Straight-through: {stats['stp']:.0f}%" if results else "Straight-through: —"
    exc_str = f"Exceptions today: {stats['review'] + stats['rejected']}"

    st.markdown(f"""
    <div class="ap-header">
      <div>
        <h1>AP Autopilot</h1>
        <p>Invoice → PO &amp; contract matching · straight-through, exceptions to a human</p>
      </div>
      <div class="ap-kpi">
        <div class="ap-kpi-stp">{stp_str}</div>
        <div class="ap-kpi-exc">{exc_str}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)


# ── render_invoice_card ───────────────────────────────────────────────────────

def _badge(action: RoutingAction) -> str:
    if action == RoutingAction.STRAIGHT_THROUGH_PAY:
        return '<span class="badge badge-stp">Straight-through</span>'
    if action == RoutingAction.HUMAN_REVIEW:
        return ('<span class="badge badge-exc">Exception</span> '
                '<span class="badge badge-route">Route to AP Clerk</span>')
    return '<span class="badge badge-rej">Rejected</span>'


def _tick() -> str:
    return '<span class="ok">✓</span>'


def _cross(suffix: str = "") -> str:
    label = f"✕ {suffix}" if suffix else "✕"
    return f'<span class="bad">{label}</span>'


def _warn(suffix: str = "") -> str:
    label = suffix if suffix else "variance"
    return f'<span class="wrn">{label}</span>'


def render_invoice_card(r: ProcessingResult) -> None:
    inv = r.invoice
    d   = r.decision
    mr  = r.match_result

    # ── look up PO for the comparison column ──────────────────────────────────
    po = PO_REGISTRY.get(inv.po_number)
    contract = CONTRACT_REGISTRY.get(inv.vendor_id)

    exceptions_set = set(d.exceptions)

    # ── heading ───────────────────────────────────────────────────────────────
    po_ref = f"Matched against {inv.po_number}"
    if contract:
        po_ref += " and the master supply agreement."
    else:
        po_ref += "."

    heading_html = f"""
    <div class="ap-card">
      <div class="ap-card-heading">
        Invoice {inv.invoice_number} · {inv.vendor_name}
        &nbsp; {_badge(d.action)}
      </div>
      <div class="ap-card-sub">{po_ref}</div>
    """

    # ── build comparison table rows ───────────────────────────────────────────
    rows_html = ""

    # Row: Vendor
    vendor_ok = (po is None or inv.vendor_id == po.vendor_id)
    vendor_match = _tick() if vendor_ok else _cross()
    vendor_po_val = po.vendor_name if po else "—"
    row_cls = "" if vendor_ok else ' class="bad"'
    rows_html += (
        f"<tr{row_cls}>"
        f"<td>Vendor</td>"
        f"<td>{inv.vendor_name}</td>"
        f"<td>{vendor_po_val}</td>"
        f"<td>{vendor_match}</td>"
        f"</tr>"
    )

    # Row: Line qty (first line only for display; show all if multi-line)
    if po and inv.line_items:
        for li in inv.line_items:
            po_li = next((x for x in po.line_items if x.line_number == li.line_number), None)
            qty_ok = po_li is not None and li.quantity == po_li.quantity_ordered
            qty_match = _tick() if qty_ok else _cross()
            po_qty_str = f"{po_li.quantity_ordered} units" if po_li else "—"
            row_cls = "" if qty_ok else ' class="bad"'
            rows_html += (
                f"<tr{row_cls}>"
                f"<td>Line qty</td>"
                f"<td>{li.quantity} units</td>"
                f"<td>{po_qty_str}</td>"
                f"<td>{qty_match}</td>"
                f"</tr>"
            )

    # Row: Unit price (first line)
    if po and inv.line_items:
        for li in inv.line_items:
            po_li = next((x for x in po.line_items if x.line_number == li.line_number), None)
            # determine reference price from contract or PO
            ref_price = None
            ref_label = ""
            if contract:
                term = next(
                    (t for t in contract.terms
                     if t.description.strip().lower() == li.description.strip().lower()),
                    None,
                )
                if term:
                    ref_price = term.agreed_unit_price
                    ref_label = f"${ref_price:,.2f} (contract)"
            if ref_price is None and po_li:
                ref_price = po_li.unit_price
                ref_label = f"${ref_price:,.2f}"

            price_ok = (ref_price is None or li.unit_price == ref_price)
            if not price_ok and ref_price is not None:
                variance = li.unit_price - ref_price
                units_total = variance * li.quantity
                sign = "+" if units_total > 0 else ""
                price_match = _cross(f"${sign}{units_total:,.0f}")
            else:
                price_match = _tick()

            row_cls = "" if price_ok else ' class="bad"'
            rows_html += (
                f"<tr{row_cls}>"
                f"<td>Unit price</td>"
                f"<td>${li.unit_price:,.2f}</td>"
                f"<td>{ref_label if ref_label else '—'}</td>"
                f"<td>{price_match}</td>"
                f"</tr>"
            )

    # Row: Total
    if po:
        po_total = sum(x.quantity_ordered * x.unit_price for x in po.line_items)
        total_ok = abs(inv.total - po_total) <= Decimal("0.05")
        total_match = _tick() if total_ok else _warn("variance")
        row_cls = "" if total_ok else ' class="bad"'
        rows_html += (
            f"<tr{row_cls}>"
            f"<td>Total</td>"
            f"<td>${inv.total:,.2f}</td>"
            f"<td>${po_total:,.2f} expected</td>"
            f"<td>{total_match}</td>"
            f"</tr>"
        )
    else:
        rows_html += (
            f"<tr><td>Total</td><td>${inv.total:,.2f}</td><td>—</td><td>—</td></tr>"
        )

    # Row: Approval
    if po:
        has_approver = bool(po.approved_by)
        threshold    = contract.auto_approval_threshold if contract else Decimal("10000.00")
        needs_approval = inv.total > threshold
        if needs_approval and not has_approver:
            appr_invoice = "—"
            appr_po      = f"required &gt; ${threshold:,.0f}"
            appr_match   = _cross("missing")
            row_cls      = ' class="bad"'
        elif has_approver:
            appr_invoice = po.approved_by
            appr_po      = "on file"
            appr_match   = _tick()
            row_cls      = ""
        else:
            appr_invoice = "—"
            appr_po      = f"not required (≤ ${threshold:,.0f})"
            appr_match   = _tick()
            row_cls      = ""

        rows_html += (
            f"<tr{row_cls}>"
            f"<td>Approval</td>"
            f"<td>{appr_invoice}</td>"
            f"<td>{appr_po}</td>"
            f"<td>{appr_match}</td>"
            f"</tr>"
        )

    table_html = f"""
      <table class="cmp">
        <thead>
          <tr><th>Field</th><th>Invoice</th><th>PO / contract</th><th>Match</th></tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    """

    # ── exception banner ──────────────────────────────────────────────────────
    banner_html = ""
    if d.action == RoutingAction.HUMAN_REVIEW and d.exceptions:
        n   = len(d.exceptions)
        exc_names = " and ".join(
            e.value.replace("_", " ") for e in d.exceptions
        )
        banner_html = f"""
      <div class="exc-banner">
        <span><b>{n} exception{'s' if n > 1 else ''} found.</b>
        {exc_names.capitalize()} detected. Held for review — not scheduled for payment.</span>
      </div>
        """
    elif d.action == RoutingAction.REJECT:
        banner_html = """
      <div class="exc-banner" style="background:#fff0f0;border-color:#f5c6c6;color:#7b1a1a;">
        <span><b>Rejected.</b> Extraction failed — required fields missing.
        Not paid. Flagged for re-submission.</span>
      </div>
        """
    elif d.action == RoutingAction.STRAIGHT_THROUGH_PAY:
        contract_ref = contract.payment_terms_days if contract else 30
        banner_html = f"""
      <div class="exc-banner" style="background:#e8faf2;border-color:#9ee5c0;color:#145a32;">
        <span><b>All fields matched.</b>
        Scheduled for payment on net-{contract_ref}.</span>
      </div>
        """

    st.markdown(
        heading_html + table_html + banner_html + "</div>",
        unsafe_allow_html=True,
    )

    # "Open for review" button rendered as a Streamlit widget (so it's clickable)
    if d.action == RoutingAction.HUMAN_REVIEW:
        col_gap, col_btn = st.columns([6, 1])
        with col_btn:
            if st.button("Open for review", key=f"review_{inv.invoice_number}",
                         type="primary", use_container_width=True):
                st.toast(f"Review queue updated for {inv.invoice_number}", icon="📋")


# ── render_bottom_strip ───────────────────────────────────────────────────────

def render_bottom_strip(results: list[ProcessingResult]) -> None:
    """
    Two-column footer card row that mirrors the screenshot bottom section:
      Left  — most recent straight-through invoice summary
      Right — latest audit log entry
    """
    stp = last_stp_result(results)
    ledger = AuditLedger(AUDIT_PATH)
    entries = ledger.all_entries()
    latest_entry = entries[-1] if entries else None

    # left panel
    if stp:
        inv = stp.invoice
        contract = CONTRACT_REGISTRY.get(inv.vendor_id)
        net = contract.payment_terms_days if contract else 30
        left_html = f"""
        <div class="sc">
          <div class="sc-title">
            {inv.invoice_number} · {inv.vendor_name}
            &nbsp;<span class="badge badge-stp">Straight-through</span>
          </div>
          <div class="sc-body">
            All fields matched {inv.po_number}.
            Scheduled for payment on net-{net}.
          </div>
        </div>
        """
    else:
        left_html = """
        <div class="sc">
          <div class="sc-title">No straight-through invoices yet</div>
          <div class="sc-body">Process an invoice to see STP results here.</div>
        </div>
        """

    # right panel
    if latest_entry:
        audit_detail = (
            f"{latest_entry.invoice_number} · "
            f"{latest_entry.event_type.value.replace('_', ' ')} — "
            f"run_{latest_entry.entry_id.hex[:8]}.json — "
            "full field-level trace persisted."
        )
        right_html = f"""
        <div class="sc">
          <div class="sc-title">Audit log</div>
          <div class="sc-body">{audit_detail}</div>
        </div>
        """
    else:
        right_html = """
        <div class="sc">
          <div class="sc-title">Audit log</div>
          <div class="sc-body">No entries yet — audit records appear after processing.</div>
        </div>
        """

    st.markdown(
        f'<div class="strip">{left_html}{right_html}</div>',
        unsafe_allow_html=True,
    )


# ── sample invoices ───────────────────────────────────────────────────────────

SAMPLES: dict[str, str] = {
    "✅ Happy path — clean match": """INVOICE

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
Currency: USD""",

    "⚠️ Price variance (+$200 over contract)": """INVOICE

Invoice Number: INV-5002
Vendor: Acme Corporation
Vendor ID: acme_corp
Purchase Order: PO-2024-001
Invoice Date: 2024-07-01

Line Items:
  1. Widget Type A     Qty: 100    Unit Price: $27.00    Total: $2,700.00
  2. Widget Type B     Qty: 50     Unit Price: $40.00    Total: $2,000.00

Subtotal: $4,700.00
Tax: $0.00
Total Due: $4,700.00
Currency: USD""",

    "⚠️ Quantity mismatch": """INVOICE

Invoice Number: INV-5003
Vendor: Globex Ltd
Vendor ID: globex_ltd
Purchase Order: PO-2024-002
Invoice Date: 2024-07-05

Line Items:
  1. Server Rack Unit  Qty: 10     Unit Price: $1,200.00  Total: $12,000.00

Subtotal: $12,000.00
Tax: $0.00
Total Due: $12,000.00
Currency: USD""",

    "⚠️ Unknown vendor": """INVOICE

Invoice Number: INV-5004
Vendor: Rogue Supplier Co
Vendor ID: rogue_vendor
Purchase Order: PO-2024-003
Invoice Date: 2024-07-10

Line Items:
  1. Laptop Stand      Qty: 20     Unit Price: $35.00    Total: $700.00

Subtotal: $700.00
Tax: $0.00
Total Due: $700.00
Currency: USD""",

    "❌ Malformed / garbled": """This is some garbled text that failed OCR.
No vendor, no PO number, no amounts. Cannot be parsed.""",
}


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    render_header(st.session_state.results)

    # ── input row ────────────────────────────────────────────────────────────
    col_input, col_select = st.columns([3, 1], gap="large")

    with col_select:
        st.markdown("**Load a sample invoice**")
        sample_choice = st.selectbox(
            "sample", label_visibility="collapsed",
            options=["(paste your own)"] + list(SAMPLES.keys()),
        )

    with col_input:
        st.markdown("**Paste invoice text**")
        default_text = SAMPLES[sample_choice] if sample_choice != "(paste your own)" else ""
        invoice_text = st.text_area(
            "invoice_text", label_visibility="collapsed",
            value=default_text, height=200,
            placeholder="Paste raw invoice text here…",
        )

    col_btn, col_clear = st.columns([1, 1])
    with col_btn:
        process_btn = st.button(
            "Process Invoice", type="primary", use_container_width=True
        )
    with col_clear:
        clear_btn = st.button(
            "Clear results", use_container_width=True
        )

    if clear_btn:
        st.session_state.results = []
        st.session_state.run_duration = 0.0
        st.session_state.total_processed = 0
        st.rerun()

    if process_btn:
        if not invoice_text.strip():
            st.warning("Paste some invoice text first.")
        else:
            agent = get_agent()
            with st.spinner("Extracting · Matching · Routing…"):
                t0 = time.time()
                result = agent.process(invoice_text)
                st.session_state.run_duration += time.time() - t0
            st.session_state.results.append(result)
            st.session_state.total_processed += 1
            st.rerun()

    # ── result cards ─────────────────────────────────────────────────────────
    if st.session_state.results:
        st.divider()
        # Most recent first
        for r in reversed(st.session_state.results):
            render_invoice_card(r)

    # ── bottom strip ──────────────────────────────────────────────────────────
    st.divider()
    render_bottom_strip(st.session_state.results)


if __name__ == "__main__":
    main()
