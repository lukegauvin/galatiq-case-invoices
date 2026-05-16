"""Shared domain models for invoice workflow stages.

The project intentionally starts with standard-library dataclasses so these
contracts work in a clean Python environment without runtime dependencies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, ClassVar, Generic, Mapping, TypeVar
from uuid import UUID


class Severity(str, Enum):
    """Issue severity levels used across deterministic gates."""

    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class GateStatus(str, Enum):
    """Outcome for a deterministic workflow gate."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class DecisionStatus(str, Enum):
    """Rule-based approval decision status."""

    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class IssueCategory(str, Enum):
    """High-level issue category used to decide whether gates block."""

    INGESTION = "ingestion"
    PARSING = "parsing"
    NORMALIZATION = "normalization"
    INVENTORY = "inventory"
    DATA_INTEGRITY = "data_integrity"
    APPROVAL = "approval"
    PAYMENT = "payment"
    SYSTEM = "system"


class PaymentStatus(str, Enum):
    """Payment attempt status."""

    SKIPPED = "skipped"
    SUCCESS = "success"
    FAILED = "failed"


class WorkflowStatus(str, Enum):
    """Terminal workflow status for orchestration results."""

    SUCCESS = "success"
    REJECTED = "rejected"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


IssueSeverity = Severity
ApprovalStatus = DecisionStatus


def _coerce_enum(value: Any, enum_type: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_type)
        raise ValueError(f"{field_name} must be one of: {allowed}") from exc


def _coerce_decimal(
    value: Any,
    field_name: str,
    *,
    required: bool = False,
    allow_negative: bool = False,
) -> Decimal | None:
    if value is None or value == "":
        if required:
            raise ValueError(f"{field_name} is required")
        return None

    if isinstance(value, Decimal):
        decimal_value = value
    else:
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{field_name} must be a valid decimal") from exc

    if not decimal_value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if decimal_value < 0 and not allow_negative:
        raise ValueError(f"{field_name} cannot be negative")
    return decimal_value


def _coerce_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_required_string(value: Any, field_name: str) -> str:
    text = _coerce_optional_string(value)
    if text is None:
        raise ValueError(f"{field_name} is required")
    return text


def _coerce_date(value: Any, field_name: str) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO date") from exc
    raise ValueError(f"{field_name} must be a date")


def _coerce_datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO datetime") from exc
        return parsed
    raise ValueError(f"{field_name} must be a datetime")


def _coerce_positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a positive integer")
    try:
        integer = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if integer <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
    return integer


def _coerce_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a boolean")


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, SerializableModel):
        return value.to_dict()
    if is_dataclass(value):
        return {
            model_field.name: _serialize_value(getattr(value, model_field.name))
            for model_field in fields(value)
        }
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    return value


class SerializableModel:
    """Small serialization helper shared by domain dataclasses."""

    def to_dict(self) -> dict[str, Any]:
        return {
            model_field.name: _serialize_value(getattr(self, model_field.name))
            for model_field in fields(self)
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


@dataclass(frozen=True, slots=True)
class LineItem(SerializableModel):
    item_name: str
    quantity: int
    unit_price: Decimal | None = None
    line_total: Decimal | None = None
    sku: str | None = None
    source_row: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "item_name", _coerce_required_string(self.item_name, "item_name")
        )
        object.__setattr__(
            self, "quantity", _coerce_positive_int(self.quantity, "quantity")
        )
        object.__setattr__(
            self, "unit_price", _coerce_decimal(self.unit_price, "unit_price")
        )
        object.__setattr__(
            self, "line_total", _coerce_decimal(self.line_total, "line_total")
        )
        object.__setattr__(self, "sku", _coerce_optional_string(self.sku))
        if self.source_row is not None:
            object.__setattr__(
                self,
                "source_row",
                _coerce_positive_int(self.source_row, "source_row"),
            )

    @property
    def is_valid(self) -> bool:
        return bool(self.item_name and self.quantity > 0)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> LineItem:
        return cls(
            item_name=payload.get("item_name", payload.get("name")),
            quantity=payload.get("quantity"),
            unit_price=payload.get("unit_price"),
            line_total=payload.get("line_total"),
            sku=payload.get("sku"),
            source_row=payload.get("source_row"),
        )


@dataclass(frozen=True, slots=True)
class Invoice(SerializableModel):
    invoice_number: str | None = None
    vendor_name: str | None = None
    vendor_address: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    line_items: list[LineItem] = field(default_factory=list)
    subtotal: Decimal | None = None
    tax_rate: Decimal | None = None
    tax_amount: Decimal | None = None
    total: Decimal | None = None
    currency: str = "USD"
    payment_terms: str | None = None
    source_path: str | None = None
    source_format: str | None = None
    parser_name: str | None = None
    extraction_confidence: Decimal | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    allowed_source_formats: ClassVar[frozenset[str]] = frozenset(
        {"pdf", "txt", "json", "csv", "xml"}
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "invoice_number", _coerce_optional_string(self.invoice_number)
        )
        object.__setattr__(self, "vendor_name", _coerce_optional_string(self.vendor_name))
        object.__setattr__(
            self, "vendor_address", _coerce_optional_string(self.vendor_address)
        )
        object.__setattr__(self, "invoice_date", _coerce_date(self.invoice_date, "invoice_date"))
        object.__setattr__(self, "due_date", _coerce_date(self.due_date, "due_date"))
        object.__setattr__(self, "subtotal", _coerce_decimal(self.subtotal, "subtotal"))
        object.__setattr__(self, "tax_rate", _coerce_decimal(self.tax_rate, "tax_rate"))
        object.__setattr__(
            self, "tax_amount", _coerce_decimal(self.tax_amount, "tax_amount")
        )
        object.__setattr__(self, "total", _coerce_decimal(self.total, "total"))
        object.__setattr__(self, "payment_terms", _coerce_optional_string(self.payment_terms))
        object.__setattr__(self, "source_path", _coerce_optional_string(self.source_path))
        object.__setattr__(self, "parser_name", _coerce_optional_string(self.parser_name))
        object.__setattr__(
            self,
            "extraction_confidence",
            _coerce_decimal(self.extraction_confidence, "extraction_confidence"),
        )

        currency = _coerce_required_string(self.currency, "currency").upper()
        if len(currency) != 3:
            raise ValueError("currency must be a 3-letter currency code")
        object.__setattr__(self, "currency", currency)

        source_format = _coerce_optional_string(self.source_format)
        if source_format is not None:
            source_format = source_format.lower()
            if source_format not in self.allowed_source_formats:
                allowed = ", ".join(sorted(self.allowed_source_formats))
                raise ValueError(f"source_format must be one of: {allowed}")
        object.__setattr__(self, "source_format", source_format)

        normalized_items = []
        for item in self.line_items:
            normalized_items.append(
                LineItem.from_dict(item) if isinstance(item, Mapping) else item
            )
        if not all(isinstance(item, LineItem) for item in normalized_items):
            raise ValueError("line_items must contain LineItem values")
        object.__setattr__(self, "line_items", normalized_items)

        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dictionary")

    @property
    def vendor(self) -> str | None:
        return self.vendor_name

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Invoice:
        data = dict(payload)
        if "vendor_name" not in data and "vendor" in data:
            data["vendor_name"] = data["vendor"]
        return cls(
            invoice_number=data.get("invoice_number"),
            vendor_name=data.get("vendor_name"),
            vendor_address=data.get("vendor_address"),
            invoice_date=data.get("invoice_date"),
            due_date=data.get("due_date"),
            line_items=data.get("line_items", []),
            subtotal=data.get("subtotal"),
            tax_rate=data.get("tax_rate"),
            tax_amount=data.get("tax_amount"),
            total=data.get("total"),
            currency=data.get("currency", "USD"),
            payment_terms=data.get("payment_terms"),
            source_path=data.get("source_path"),
            source_format=data.get("source_format"),
            parser_name=data.get("parser_name"),
            extraction_confidence=data.get("extraction_confidence"),
            metadata=data.get("metadata", {}),
        )


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ValidationIssue(SerializableModel):
    code: str
    severity: Severity
    category: IssueCategory
    message: str
    field_path: str | None = None
    item_name: str | None = None
    expected: Any | None = None
    actual: Any | None = None
    source: str = "validator"
    recoverable: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _coerce_required_string(self.code, "code"))
        object.__setattr__(
            self, "severity", _coerce_enum(self.severity, Severity, "severity")
        )
        object.__setattr__(
            self, "category", _coerce_enum(self.category, IssueCategory, "category")
        )
        object.__setattr__(self, "message", _coerce_required_string(self.message, "message"))
        object.__setattr__(self, "field_path", _coerce_optional_string(self.field_path))
        object.__setattr__(self, "item_name", _coerce_optional_string(self.item_name))
        object.__setattr__(self, "source", _coerce_required_string(self.source, "source"))
        object.__setattr__(
            self, "recoverable", _coerce_bool(self.recoverable, "recoverable")
        )

    @property
    def blocks_validation(self) -> bool:
        return self.severity is Severity.BLOCKING and self.category in {
            IssueCategory.INVENTORY,
            IssueCategory.DATA_INTEGRITY,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ValidationIssue:
        return cls(
            code=payload.get("code"),
            severity=payload.get("severity"),
            category=payload.get("category"),
            message=payload.get("message"),
            field_path=payload.get("field_path"),
            item_name=payload.get("item_name"),
            expected=payload.get("expected"),
            actual=payload.get("actual"),
            source=payload.get("source", "validator"),
            recoverable=payload.get("recoverable", True),
        )


@dataclass(frozen=True, slots=True)
class AgentResult(SerializableModel, Generic[T]):
    stage: str
    ok: bool
    value: T | None = None
    issues: list[ValidationIssue] = field(default_factory=list)
    reasoning: str | None = None
    confidence: Decimal | None = None
    attempts: int = 1
    raw_response: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", _coerce_required_string(self.stage, "stage"))
        object.__setattr__(self, "ok", _coerce_bool(self.ok, "ok"))
        object.__setattr__(
            self, "confidence", _coerce_decimal(self.confidence, "confidence")
        )
        object.__setattr__(
            self, "attempts", _coerce_positive_int(self.attempts, "attempts")
        )
        object.__setattr__(self, "reasoning", _coerce_optional_string(self.reasoning))
        normalized_issues = [
            ValidationIssue.from_dict(issue) if isinstance(issue, Mapping) else issue
            for issue in self.issues
        ]
        if not all(isinstance(issue, ValidationIssue) for issue in normalized_issues):
            raise ValueError("issues must contain ValidationIssue values")
        object.__setattr__(self, "issues", normalized_issues)
        if self.raw_response is not None and not isinstance(self.raw_response, dict):
            raise ValueError("raw_response must be a dictionary")


@dataclass(frozen=True, slots=True)
class ApprovalDecision(SerializableModel):
    status: DecisionStatus
    approved_by: str = "rules"
    reasoning: str = ""
    blocking_issue_codes: list[str] = field(default_factory=list)
    requires_human_review: bool = False
    reflection_notes: list[str] = field(default_factory=list)
    confidence: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "status", _coerce_enum(self.status, DecisionStatus, "status")
        )
        object.__setattr__(
            self, "approved_by", _coerce_required_string(self.approved_by, "approved_by")
        )
        object.__setattr__(self, "reasoning", str(self.reasoning).strip())
        object.__setattr__(
            self,
            "blocking_issue_codes",
            [
                _coerce_required_string(code, "blocking_issue_codes")
                for code in self.blocking_issue_codes
            ],
        )
        object.__setattr__(
            self,
            "requires_human_review",
            _coerce_bool(self.requires_human_review, "requires_human_review"),
        )
        object.__setattr__(
            self,
            "reflection_notes",
            [str(note).strip() for note in self.reflection_notes if str(note).strip()],
        )
        object.__setattr__(
            self, "confidence", _coerce_decimal(self.confidence, "confidence")
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ApprovalDecision:
        return cls(
            status=payload.get("status"),
            approved_by=payload.get("approved_by", "rules"),
            reasoning=payload.get("reasoning", ""),
            blocking_issue_codes=payload.get("blocking_issue_codes", []),
            requires_human_review=payload.get("requires_human_review", False),
            reflection_notes=payload.get("reflection_notes", []),
            confidence=payload.get("confidence"),
        )


@dataclass(frozen=True, slots=True)
class PaymentResult(SerializableModel):
    status: PaymentStatus
    payment_id: str | None = None
    idempotency_key: str | None = None
    vendor_name: str | None = None
    amount: Decimal | None = None
    currency: str = "USD"
    error_message: str | None = None
    attempted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "status", _coerce_enum(self.status, PaymentStatus, "status")
        )
        object.__setattr__(self, "payment_id", _coerce_optional_string(self.payment_id))
        object.__setattr__(
            self, "idempotency_key", _coerce_optional_string(self.idempotency_key)
        )
        object.__setattr__(self, "vendor_name", _coerce_optional_string(self.vendor_name))
        object.__setattr__(self, "amount", _coerce_decimal(self.amount, "amount"))
        object.__setattr__(self, "error_message", _coerce_optional_string(self.error_message))
        object.__setattr__(
            self, "attempted_at", _coerce_datetime(self.attempted_at, "attempted_at")
        )

        currency = _coerce_required_string(self.currency, "currency").upper()
        if len(currency) != 3:
            raise ValueError("currency must be a 3-letter currency code")
        object.__setattr__(self, "currency", currency)

        if self.status is PaymentStatus.SUCCESS and self.payment_id is None:
            raise ValueError("payment_id is required for successful payments")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PaymentResult:
        return cls(
            status=payload.get("status"),
            payment_id=payload.get("payment_id"),
            idempotency_key=payload.get("idempotency_key"),
            vendor_name=payload.get("vendor_name"),
            amount=payload.get("amount"),
            currency=payload.get("currency", "USD"),
            error_message=payload.get("error_message"),
            attempted_at=payload.get(
                "attempted_at", datetime.now(timezone.utc).isoformat()
            ),
        )


@dataclass(frozen=True, slots=True)
class GateDecision(SerializableModel):
    gate: str
    status: GateStatus
    reason: str
    issue_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate", _coerce_required_string(self.gate, "gate"))
        object.__setattr__(
            self, "status", _coerce_enum(self.status, GateStatus, "status")
        )
        object.__setattr__(self, "reason", _coerce_required_string(self.reason, "reason"))
        object.__setattr__(
            self,
            "issue_codes",
            tuple(_coerce_required_string(code, "issue_codes") for code in self.issue_codes),
        )

    @property
    def passed(self) -> bool:
        return self.status is GateStatus.PASSED

    @classmethod
    def passed_decision(
        cls, gate: str, reason: str, issue_codes: tuple[str, ...] = ()
    ) -> GateDecision:
        return cls(gate=gate, status=GateStatus.PASSED, reason=reason, issue_codes=issue_codes)

    @classmethod
    def failed_decision(
        cls, gate: str, reason: str, issue_codes: tuple[str, ...] = ()
    ) -> GateDecision:
        return cls(gate=gate, status=GateStatus.FAILED, reason=reason, issue_codes=issue_codes)

    @classmethod
    def skipped_decision(
        cls, gate: str, reason: str, issue_codes: tuple[str, ...] = ()
    ) -> GateDecision:
        return cls(gate=gate, status=GateStatus.SKIPPED, reason=reason, issue_codes=issue_codes)


@dataclass(frozen=True, slots=True)
class WorkflowResult(SerializableModel):
    run_id: UUID
    status: WorkflowStatus
    invoice: Invoice | None = None
    validation_issues: list[ValidationIssue] = field(default_factory=list)
    approval_decision: ApprovalDecision | None = None
    payment_result: PaymentResult | None = None
    audit_log_path: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, UUID):
            object.__setattr__(self, "run_id", UUID(str(self.run_id)))
        object.__setattr__(
            self, "status", _coerce_enum(self.status, WorkflowStatus, "status")
        )
        object.__setattr__(
            self, "audit_log_path", _coerce_optional_string(self.audit_log_path)
        )
        object.__setattr__(
            self, "started_at", _coerce_datetime(self.started_at, "started_at")
        )
        object.__setattr__(
            self, "completed_at", _coerce_datetime(self.completed_at, "completed_at")
        )
        normalized_issues = [
            ValidationIssue.from_dict(issue) if isinstance(issue, Mapping) else issue
            for issue in self.validation_issues
        ]
        if not all(isinstance(issue, ValidationIssue) for issue in normalized_issues):
            raise ValueError("validation_issues must contain ValidationIssue values")
        object.__setattr__(self, "validation_issues", normalized_issues)


__all__ = [
    "AgentResult",
    "ApprovalDecision",
    "ApprovalStatus",
    "DecisionStatus",
    "GateDecision",
    "GateStatus",
    "Invoice",
    "IssueCategory",
    "IssueSeverity",
    "LineItem",
    "PaymentResult",
    "PaymentStatus",
    "Severity",
    "ValidationIssue",
    "WorkflowResult",
    "WorkflowStatus",
]

