from __future__ import annotations

import json
import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from galatiq_invoices.domain.gates import (  # noqa: E402
    evaluate_approval_gate,
    evaluate_ingestion_gate,
    evaluate_payment_gate,
    evaluate_validation_gate,
)
from galatiq_invoices.domain.models import (  # noqa: E402
    ApprovalDecision,
    DecisionStatus,
    GateStatus,
    Invoice,
    IssueCategory,
    LineItem,
    PaymentResult,
    PaymentStatus,
    Severity,
    ValidationIssue,
)


class DomainModelTests(unittest.TestCase):
    def test_line_item_validates_and_coerces_money(self) -> None:
        item = LineItem(item_name="Widget", quantity="2", unit_price="10.25")

        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.unit_price, Decimal("10.25"))
        self.assertTrue(item.is_valid)

    def test_line_item_rejects_invalid_quantity(self) -> None:
        with self.assertRaisesRegex(ValueError, "quantity must be greater than zero"):
            LineItem(item_name="Widget", quantity=0)

    def test_invoice_from_dict_accepts_vendor_alias_and_serializes(self) -> None:
        invoice = Invoice.from_dict(
            {
                "invoice_number": "INV-1",
                "vendor": "Acme Supplies",
                "due_date": "2026-06-01",
                "line_items": [
                    {
                        "item_name": "Widget",
                        "quantity": "3",
                        "line_total": "30.00",
                    }
                ],
                "total": "30.00",
                "source_format": "JSON",
            }
        )

        self.assertEqual(invoice.vendor_name, "Acme Supplies")
        self.assertEqual(invoice.due_date, date(2026, 6, 1))
        self.assertEqual(invoice.source_format, "json")

        payload = invoice.to_dict()
        self.assertEqual(payload["total"], "30.00")
        self.assertEqual(payload["due_date"], "2026-06-01")
        self.assertEqual(payload["line_items"][0]["line_total"], "30.00")

    def test_validation_issue_serializes_enum_values(self) -> None:
        issue = ValidationIssue(
            code="UNKNOWN_SKU",
            severity=Severity.BLOCKING,
            category=IssueCategory.INVENTORY,
            message="SKU not found",
        )

        self.assertTrue(issue.blocks_validation)
        self.assertEqual(
            json.loads(issue.to_json()),
            {
                "actual": None,
                "category": "inventory",
                "code": "UNKNOWN_SKU",
                "expected": None,
                "field_path": None,
                "item_name": None,
                "message": "SKU not found",
                "recoverable": True,
                "severity": "blocking",
                "source": "validator",
            },
        )

    def test_payment_result_validates_success_payment_id_and_serializes(self) -> None:
        with self.assertRaisesRegex(ValueError, "payment_id is required"):
            PaymentResult(status=PaymentStatus.SUCCESS)

        attempted_at = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
        payment = PaymentResult(
            status="success",
            payment_id="pay_123",
            amount="42.00",
            attempted_at=attempted_at,
        )

        self.assertEqual(payment.status, PaymentStatus.SUCCESS)
        self.assertEqual(payment.amount, Decimal("42.00"))
        self.assertEqual(payment.to_dict()["attempted_at"], attempted_at.isoformat())


class GateBehaviorTests(unittest.TestCase):
    def valid_invoice(self) -> Invoice:
        return Invoice(
            invoice_number="INV-1",
            vendor_name="Acme Supplies",
            due_date=date(2026, 6, 1),
            total=Decimal("25.00"),
            line_items=[
                LineItem(item_name="Widget", quantity=1, line_total=Decimal("25.00"))
            ],
        )

    def test_ingestion_gate_requires_core_fields_and_line_item(self) -> None:
        passing = evaluate_ingestion_gate(self.valid_invoice())
        missing = evaluate_ingestion_gate(
            Invoice(invoice_number="INV-1", vendor_name="Acme Supplies")
        )

        self.assertEqual(passing.status, GateStatus.PASSED)
        self.assertTrue(passing.passed)
        self.assertEqual(missing.status, GateStatus.FAILED)
        self.assertIn("due_date", missing.reason)
        self.assertIn("total", missing.reason)
        self.assertIn("line_items", missing.reason)

    def test_validation_gate_only_blocks_inventory_and_data_integrity(self) -> None:
        warning = ValidationIssue(
            code="LATE_DUE_DATE",
            severity=Severity.WARNING,
            category=IssueCategory.DATA_INTEGRITY,
            message="Due date is unusual",
        )
        payment_blocker = ValidationIssue(
            code="BANK_DOWN",
            severity=Severity.BLOCKING,
            category=IssueCategory.PAYMENT,
            message="Payment service unavailable",
        )
        inventory_blocker = ValidationIssue(
            code="OUT_OF_STOCK",
            severity=Severity.BLOCKING,
            category=IssueCategory.INVENTORY,
            message="Quantity exceeds stock",
        )

        self.assertEqual(
            evaluate_validation_gate([warning, payment_blocker]).status,
            GateStatus.PASSED,
        )
        failed = evaluate_validation_gate([inventory_blocker])
        self.assertEqual(failed.status, GateStatus.FAILED)
        self.assertEqual(failed.issue_codes, ("OUT_OF_STOCK",))

    def test_approval_gate_requires_validation_pass_and_approved_decision(self) -> None:
        validation_passed = evaluate_validation_gate([])
        validation_failed = evaluate_validation_gate(
            [
                ValidationIssue(
                    code="TOTAL_MISMATCH",
                    severity=Severity.BLOCKING,
                    category=IssueCategory.DATA_INTEGRITY,
                    message="Line totals do not match invoice total",
                )
            ]
        )

        approved = ApprovalDecision(status=DecisionStatus.APPROVED)
        needs_review = ApprovalDecision(status=DecisionStatus.NEEDS_REVIEW)

        self.assertEqual(
            evaluate_approval_gate(validation_passed, approved).status,
            GateStatus.PASSED,
        )
        self.assertEqual(
            evaluate_approval_gate(validation_failed, approved).status,
            GateStatus.FAILED,
        )
        self.assertEqual(
            evaluate_approval_gate(validation_passed, needs_review).status,
            GateStatus.FAILED,
        )

    def test_payment_gate_only_runs_after_approval_passes(self) -> None:
        validation_passed = evaluate_validation_gate([])
        approval_passed = evaluate_approval_gate(
            validation_passed, ApprovalDecision(status=DecisionStatus.APPROVED)
        )
        approval_failed = evaluate_approval_gate(
            validation_passed, ApprovalDecision(status=DecisionStatus.REJECTED)
        )

        self.assertEqual(evaluate_payment_gate(approval_passed).status, GateStatus.PASSED)
        self.assertEqual(evaluate_payment_gate(approval_failed).status, GateStatus.SKIPPED)


if __name__ == "__main__":
    unittest.main()
