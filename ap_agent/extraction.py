"""
Extraction agent — turns raw invoice text into a validated Invoice schema.

Uses the OpenRouter API (OpenAI-compatible) with function-calling so the model
is forced to return structured JSON that is then validated by Pydantic.
A malformed extraction raises ValidationError, which the orchestrator catches
and marks the invoice as REJECTED.

Env vars required (set in .env):
    OPENROUTER_API_KEY   — your key from https://openrouter.ai/keys
    OPENROUTER_BASE_URL  — defaults to https://openrouter.ai/api/v1
"""

from __future__ import annotations

import json
import os
from decimal import Decimal

from openai import OpenAI
from pydantic import ValidationError

from .schemas import (
    ExceptionReason,
    Invoice,
    InvoiceStatus,
    LineItem,
)

# ---------------------------------------------------------------------------
# Tool / function schema that the model must call
# ---------------------------------------------------------------------------

EXTRACTION_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_invoice",
        "description": (
            "Submit the structured fields extracted from the raw invoice text. "
            "Every required field must be populated. Use ISO-8601 dates (YYYY-MM-DD). "
            "Monetary values must be numeric strings (e.g. '1234.56')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_number": {"type": "string"},
                "vendor_id": {"type": "string"},
                "vendor_name": {"type": "string"},
                "po_number": {"type": "string"},
                "invoice_date": {"type": "string", "description": "YYYY-MM-DD"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD or null"},
                "currency": {"type": "string", "default": "USD"},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "line_number": {"type": "integer"},
                            "description": {"type": "string"},
                            "quantity": {"type": "string"},
                            "unit_price": {"type": "string"},
                            "total": {"type": "string"},
                        },
                        "required": [
                            "line_number",
                            "description",
                            "quantity",
                            "unit_price",
                            "total",
                        ],
                    },
                },
                "subtotal": {"type": "string"},
                "tax": {"type": "string", "default": "0.00"},
                "total": {"type": "string"},
            },
            "required": [
                "invoice_number",
                "vendor_id",
                "vendor_name",
                "po_number",
                "invoice_date",
                "line_items",
                "subtotal",
                "total",
            ],
        },
    },
}

SYSTEM_PROMPT = (
    "You are an AP data-extraction specialist. "
    "Read the invoice text carefully and call submit_invoice with every field "
    "you can extract. Do not infer or fabricate values — if a field is absent, "
    "omit the optional field or leave it null. "
    "Vendor ID should be derived from the vendor name if not explicitly stated "
    "(use snake_case, e.g. 'acme_corp')."
)


# ---------------------------------------------------------------------------
# Extractor class
# ---------------------------------------------------------------------------


_DEFAULT_MODEL = "nousresearch/hermes-3-llama-3.1-405b:free"
_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# Free-model fallback chain — tried in order when rate-limited
_FALLBACK_MODELS = [
    "nousresearch/hermes-3-llama-3.1-405b:free",   # 405B — best free model for structured output
    "nvidia/nemotron-3-ultra-550b-a55b:free",       # 550B — largest free model
    "meta-llama/llama-3.3-70b-instruct:free",       # 70B — reliable fallback
    "google/gemma-4-31b-it:free",                   # Google 31B — solid fallback
    "openai/gpt-oss-20b:free",                      # OpenAI OSS 20B — last resort
]


class ExtractionAgent:
    """Calls the OpenRouter API to extract and validate invoice fields."""

    def __init__(self, model: str = _DEFAULT_MODEL) -> None:
        self.client = OpenAI(
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url=os.environ.get("OPENROUTER_BASE_URL", _DEFAULT_BASE_URL),
        )
        self.model = model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, raw_text: str) -> Invoice:
        """
        Parse *raw_text* into a validated Invoice.
        Automatically retries with fallback free models on rate-limit errors.

        Raises
        ------
        ValidationError
            When the extracted payload is structurally invalid.
        ValueError
            When all models fail to call the tool.
        """
        import time
        from openai import RateLimitError, NotFoundError

        models_to_try = (
            [self.model]
            + [m for m in _FALLBACK_MODELS if m != self.model]
        )

        last_error: Exception = ValueError("No models available.")
        for model in models_to_try:
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"Extract the invoice:\n\n{raw_text}"},
                    ],
                    tools=[EXTRACTION_TOOL],
                    tool_choice={"type": "function", "function": {"name": "submit_invoice"}},
                    temperature=0,
                )

                choice = response.choices[0]
                tool_calls = choice.message.tool_calls

                if not tool_calls:
                    raise ValueError(
                        f"Model {model} did not call submit_invoice — cannot extract."
                    )

                payload: dict = json.loads(tool_calls[0].function.arguments)
                return self._build_invoice(payload, raw_text)

            except (RateLimitError, NotFoundError) as exc:
                print(f"  [warn] Model {model} unavailable ({exc.status_code}), trying next...")
                last_error = exc
                time.sleep(2)
                continue

        raise last_error

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_invoice(payload: dict, raw_text: str) -> Invoice:
        """Convert the raw extracted dict into a validated Invoice."""

        def to_decimal(value: object, field: str) -> Decimal:
            """Safely coerce a value to Decimal, raising ValueError on failure."""
            try:
                return Decimal(str(value).replace(",", "").strip())
            except Exception:
                raise ValueError(
                    f"Cannot convert field '{field}' value {value!r} to Decimal."
                )

        # Coerce line-item numerics to Decimal
        line_items = []
        for raw_li in payload.get("line_items", []):
            line_items.append(
                LineItem(
                    line_number=int(raw_li["line_number"]),
                    description=raw_li["description"],
                    quantity=to_decimal(raw_li["quantity"], "quantity"),
                    unit_price=to_decimal(raw_li["unit_price"], "unit_price"),
                    total=to_decimal(raw_li["total"], "total"),
                )
            )

        invoice_data = {
            "invoice_number": payload["invoice_number"],
            "vendor_id": payload["vendor_id"],
            "vendor_name": payload["vendor_name"],
            "po_number": payload["po_number"],
            "invoice_date": payload["invoice_date"],
            "due_date": payload.get("due_date"),
            "line_items": line_items,
            "subtotal": to_decimal(payload["subtotal"], "subtotal"),
            "tax": to_decimal(payload.get("tax", "0.00"), "tax"),
            "total": to_decimal(payload["total"], "total"),
            "currency": payload.get("currency", "USD"),
            "raw_text": raw_text,
            "status": InvoiceStatus.PENDING,
        }

        return Invoice(**invoice_data)

    @staticmethod
    def make_rejected_invoice(raw_text: str, error: Exception) -> Invoice:
        """
        Create a minimal stub Invoice with REJECTED status when extraction
        fails so the audit trail always has a record.
        """
        return Invoice(
            invoice_number="UNREADABLE",
            vendor_id="unknown",
            vendor_name="Unknown",
            po_number="unknown",
            invoice_date="1970-01-01",
            line_items=[
                LineItem(
                    line_number=1,
                    description="EXTRACTION FAILED",
                    quantity=Decimal("1"),
                    unit_price=Decimal("0.01"),
                    total=Decimal("0.01"),
                )
            ],
            subtotal=Decimal("0.01"),
            tax=Decimal("0.00"),
            total=Decimal("0.01"),
            status=InvoiceStatus.REJECTED,
            exception_reasons=[ExceptionReason.MALFORMED_EXTRACTION],
            raw_text=raw_text,
        )
