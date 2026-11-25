"""Orders logged when placed, redaction before anything leaves this computer, and invoice reading."""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from .gateway import Gateway
from .ledger import Actor, Ledger, LedgerError, ProjectState


class ProcurementError(LedgerError):
    pass


PARTY = re.compile(r"\b(ship(ping)?\s*to|bill(ing)?\s*to|deliver(y)?\s*(to|address)|sold\s*to|consignee|attention|attn|recipient|customer\s*(name|details)|buyer|contact\s*(name|person)|shipping address|billing address)\b", re.I)
ADDRESS = re.compile(r"\b(road|rd\.?|street|avenue|ave\.?|drive|lane|blk|block|crescent|residence|hostel|tower|unit|postal|floor|campus)\b|#\d+-\d+|\b[a-z]+\s+\d{5,6}\b", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"\+\s*\d{1,3}[\s-]*\d{3,4}[\s-]*\d{3,4}|\b(tel|phone|mobile|fax|hp)\b[^\n]*", re.I)
CARD = re.compile(r"(visa|mastercard|amex|master|card)\D{0,20}\d{4}\b", re.I)
GUARD = re.compile(r"[\w.+-]+@[\w-]+\.\w|(visa|mastercard|amex)\D{0,20}\d{4}\b", re.I)
END_OF_PARTY = re.compile(r"\b(sold\s*by|seller|supplier|item|description|qty|quantity|product)\b", re.I)
HAS_MONEY = re.compile(r"\d+\.\d{2}|invoice|order|date", re.I)


def redact(text: str, people: list[str] | None = None) -> str:
    """Remove personal details before text can go to an AI provider."""
    names = [re.escape(p.strip()) for p in (people or []) if len(p.strip()) >= 2]
    named = re.compile("|".join(names), re.I) if names else None
    out: list[str] = []
    hide = 0
    for line in text.splitlines():
        if PARTY.search(line):
            hide = 5
            out.append("[PARTY DETAILS REMOVED]")
            continue
        if hide > 0 and END_OF_PARTY.search(line):
            hide = 0
        if hide > 0:
            hide -= 1
            if HAS_MONEY.search(line):
                out.append(re.sub(r"^\s*\S.{0,44}?\s{3,}", "[REMOVED]   ", line))
            continue
        if ADDRESS.search(line):
            out.append("[ADDRESS REMOVED]")
            continue
        for pattern, label in ((EMAIL, "[EMAIL]"), (PHONE, "[PHONE]"), (CARD, "[CARD]")):
            line = pattern.sub(label, line)
        if named:
            line = named.sub("[PERSON]", line)
        if line.strip():
            out.append(line)
    return "\n".join(out)


def is_clean(text: str, people: list[str] | None = None) -> bool:
    """A last independent check. Anything suspicious stays on this computer."""
    if GUARD.search(text):
        return False
    return not any(p.strip() and p.strip().lower() in text.lower() for p in (people or []))


class OrderOut(BaseModel):
    supplier: str | None
    reference: str | None
    order_date: str | None
    currency: str | None
    items: str | None = Field(description="Short summary of what was bought")
    subtotal: float | None
    shipping: float | None
    tax: float | None
    total: float | None = Field(description="Total in the stated currency. Null if not printed. Never convert.")
    uncertain: list[str] = Field(description="Every field you could not determine")


def order_flags(fields: dict[str, Any], source_text: str = "") -> list[str]:
    flags = []
    for key, label in (("order_date", "No order date"), ("currency", "No currency"), ("total", "No total"), ("supplier", "No supplier"), ("buyer", "No buyer"), ("funding", "No funding source")):
        if fields.get(key) in (None, ""):
            flags.append(label)
    if re.search(r"\b(CPT|DAP|DDU)\b|duties|customs|import\s+tax", source_text, re.I) and not fields.get("duties"):
        flags.append("Import duties may be due and aren't included")
    return flags


def landed_cost(fields: dict[str, Any]) -> float | None:
    if fields.get("total") is None:
        return None
    return round(float(fields["total"]) + float(fields.get("duties") or 0), 2)


def add_order(ledger: Ledger, fields: dict[str, Any], actor: Actor, source_text: str = "") -> str:
    from . import traceability

    due = traceability.tests_due(ledger, "before_buying") if actor.kind == "human" else []
    if due:
        raise ProcurementError(f"Do these tests before ordering: {', '.join(t['key'] for t in due)}.")
    fields = {**fields, "flags": order_flags(fields, source_text), "status": fields.get("status") or "Ordered"}
    record = ledger.propose(actor, "order", **fields)
    if actor.kind == "human":
        ledger.append(actor, "record.confirmed", id=record)
    return record


def orders_view(state: ProjectState) -> dict[str, Any]:
    orders = [o for o in state.of_type("order") if o["status"] != "rejected"]
    by_currency: dict[str, float] = {}
    for order in orders:
        cost = landed_cost(order["fields"])
        if cost is not None and order["status"] == "confirmed":
            currency = order["fields"].get("currency") or "Unknown"
            by_currency[currency] = round(by_currency.get(currency, 0) + cost, 2)
    return {"orders": [{**o, "landed": landed_cost(o["fields"])} for o in orders], "totals": by_currency}


async def read_invoice(ledger: Ledger, gateway: Gateway, redacted_text: str, people: list[str]) -> str:
    if not is_clean(redacted_text, people):
        raise ProcurementError("Personal details may remain in this text, so it wasn't sent.")
    output: OrderOut = await gateway.run(agent="procurement", values={"invoice": redacted_text}, output_type=OrderOut, request_limit=3)
    fields = output.model_dump()
    fields["buyer"] = None
    fields["funding"] = None
    return add_order(ledger, fields, Actor.agent("procurement"), redacted_text)
