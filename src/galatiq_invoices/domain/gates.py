"""Deterministic gate helpers for invoice workflow transitions."""

from __future__ import annotations

from collections.abc import Sequence

from galatiq_invoices.domain.models import (
    ApprovalDecision,
    DecisionStatus,
    GateDecision,
    Invoice,
    LineItem,
    ValidationIssue,
)


def _is_valid_line_item(item: LineItem) -> bool:
    return (
        isinstance(item, LineItem)
        and bool(item.item_name)
        and isinstance(item.quantity, int)
        and item.quantity > 0
        and (item.unit_price is None or item.unit_price >= 0)
        and (item.line_total is None or item.line_total >= 0)
    )


def evaluate_ingestion_gate(invoice: Invoice | None) -> GateDecision:
    """Pass only when the canonical invoice has the required intake fields."""

    if invoice is None:
        return GateDecision.failed_decision("ingestion", "invoice is missing")

    missing_fields = []
    if not invoice.invoice_number:
        missing_fields.append("invoice_number")
    if not invoice.vendor_name:
        missing_fields.append("vendor")
    if invoice.due_date is None:
        missing_fields.append("due_date")
    if invoice.total is None:
        missing_fields.append("total")
    if not any(_is_valid_line_item(item) for item in invoice.line_items):
        missing_fields.append("line_items")

    if missing_fields:
        return GateDecision.failed_decision(
            "ingestion",
            "missing required invoice data: " + ", ".join(missing_fields),
        )
    return GateDecision.passed_decision(
        "ingestion", "invoice contains required fields and a valid line item"
    )


def evaluate_validation_gate(issues: Sequence[ValidationIssue]) -> GateDecision:
    """Pass only when no blocking inventory/data-integrity issues exist."""

    blocking_issue_codes = tuple(
        issue.code for issue in issues if issue.blocks_validation
    )
    if blocking_issue_codes:
        return GateDecision.failed_decision(
            "validation",
            "blocking inventory or data-integrity issues exist",
            blocking_issue_codes,
        )
    return GateDecision.passed_decision(
        "validation", "no blocking inventory or data-integrity issues"
    )


def evaluate_approval_gate(
    validation_result: GateDecision, approval_decision: ApprovalDecision | None
) -> GateDecision:
    """Pass only when validation passed and approval rules approve."""

    if not validation_result.passed:
        return GateDecision.failed_decision(
            "approval",
            "validation gate did not pass",
            validation_result.issue_codes,
        )
    if approval_decision is None:
        return GateDecision.failed_decision("approval", "approval decision is missing")
    if approval_decision.status is not DecisionStatus.APPROVED:
        return GateDecision.failed_decision(
            "approval",
            f"approval rules returned {approval_decision.status.value}",
            tuple(approval_decision.blocking_issue_codes),
        )
    if approval_decision.requires_human_review:
        return GateDecision.failed_decision(
            "approval", "approval rules require human review"
        )
    if approval_decision.blocking_issue_codes:
        return GateDecision.failed_decision(
            "approval",
            "approval decision contains blocking issue codes",
            tuple(approval_decision.blocking_issue_codes),
        )
    return GateDecision.passed_decision("approval", "approval rules approved")


def evaluate_payment_gate(approval_result: GateDecision) -> GateDecision:
    """Allow payment to run only after the approval gate passes."""

    if approval_result.passed:
        return GateDecision.passed_decision(
            "payment", "approval gate passed; payment may run"
        )
    return GateDecision.skipped_decision(
        "payment",
        "approval gate did not pass; payment must not run",
        approval_result.issue_codes,
    )


ingestion_gate = evaluate_ingestion_gate
validation_gate = evaluate_validation_gate
approval_gate = evaluate_approval_gate
payment_gate = evaluate_payment_gate


__all__ = [
    "approval_gate",
    "evaluate_approval_gate",
    "evaluate_ingestion_gate",
    "evaluate_payment_gate",
    "evaluate_validation_gate",
    "ingestion_gate",
    "payment_gate",
    "validation_gate",
]

