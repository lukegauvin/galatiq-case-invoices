# Invoice Processing Architecture Brief

This is a pre-implementation design artifact. It defines module boundaries, typed contracts, gates, retries, audit events, and file layout only. No business logic is implemented here.

## Architecture Decisions

- Runtime: Python 3.11+.
- CLI: `typer`, because command structure, typed options, and testability are better than raw `argparse`.
- Data contracts: `pydantic` v2 models with `Decimal` for money and `date` for dates.
- Orchestration: custom deterministic pipeline. The business case asks for a multi-agent system, but the MVP should keep state transitions explicit and testable. Agent behavior is exposed behind protocols so Grok, another LLM, or a local simulator can be swapped without changing workflow code.
- Parsing: adapter registry keyed by file extension. Each adapter converts source files into `AgentResult[RawInvoicePayload]`; normalization converts raw payloads into canonical `Invoice`.
- Validation: deterministic inventory and data-integrity checks against SQLite. Agent critique may explain or classify issues, but cannot override deterministic validation.
- Approval/reflection: rule-based approval decision plus an optional reflection loop that critiques the decision. The final approval still passes through deterministic gates.
- Payment: mock local payment client with idempotency key based on workflow run id and invoice number.
- Audit logging: append-only structured JSONL for observability, plus optional SQLite persistence for queryable run history.
- Persistence/setup: SQLite for inventory seed data, workflow runs, payment attempts, and normalized invoice snapshots.
- Tests: unit tests for contracts/adapters/validation/payment, integration tests for full invoice examples, and snapshot-style audit event tests.

## Component Map

```text
CLI
  -> loads runtime config
  -> calls orchestrator
  -> renders WorkflowResult

Orchestrator
  -> creates workflow run id
  -> calls each stage in order
  -> enforces deterministic gates
  -> handles retries and fail-fast transitions
  -> emits audit events through AuditLogger

Ingestion
  -> verifies source path
  -> fingerprints source document
  -> selects parser adapter
  -> returns SourceDocument

Parsing Adapters
  -> JSON adapter
  -> CSV adapter
  -> XML adapter
  -> TXT adapter
  -> PDF adapter
  -> return raw structured payload plus extraction confidence

Normalization
  -> maps adapter payloads into canonical Invoice
  -> preserves source metadata and parser notes
  -> returns canonical Invoice plus normalization issues

Validation
  -> checks data integrity
  -> checks inventory database
  -> checks totals and currency consistency
  -> returns ValidationIssue list

Approval and Reflection
  -> makes initial rule-based ApprovalDecision
  -> optionally asks agent/reflection client to critique decision
  -> returns final ApprovalDecision with reasoning trace

Payment
  -> pays only approved invoices with no blocking issues
  -> calls mock payment client
  -> returns PaymentResult

Audit Logging
  -> writes every stage start, success, retry, skip, and failure
  -> never mutates business decisions

Persistence and Setup
  -> initializes inventory database
  -> records workflow runs, invoices, decisions, payments
  -> supports repeatable local demo state

Tests
  -> exercise each contract and stage gate
  -> run end-to-end fixtures from data/invoices
```

## Core Typed Contracts

The concrete implementation should place these contracts in `src/galatiq_invoices/domain/models.py`. Extra internal types such as `SourceDocument`, `RawInvoicePayload`, and `WorkflowContext` may be added, but the contracts below are the public domain surface.

```python
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field


class IssueSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class ApprovalStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class PaymentStatus(str, Enum):
    SKIPPED = "skipped"
    SUCCESS = "success"
    FAILED = "failed"


class WorkflowStatus(str, Enum):
    SUCCESS = "success"
    REJECTED = "rejected"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class LineItem(BaseModel):
    item_name: str
    quantity: int
    unit_price: Decimal | None = None
    line_total: Decimal | None = None
    sku: str | None = None
    source_row: int | None = None


class Invoice(BaseModel):
    invoice_number: str | None = None
    vendor_name: str | None = None
    vendor_address: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Decimal | None = None
    tax_rate: Decimal | None = None
    tax_amount: Decimal | None = None
    total: Decimal | None = None
    currency: str = "USD"
    payment_terms: str | None = None
    source_path: str
    source_format: Literal["pdf", "txt", "json", "csv", "xml"]
    parser_name: str
    extraction_confidence: Decimal | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


T = TypeVar("T")


class AgentResult(BaseModel, Generic[T]):
    stage: str
    ok: bool
    value: T | None = None
    issues: list["ValidationIssue"] = Field(default_factory=list)
    reasoning: str | None = None
    confidence: Decimal | None = None
    attempts: int = 1
    raw_response: dict[str, Any] | None = None


class ValidationIssue(BaseModel):
    code: str
    severity: IssueSeverity
    message: str
    field_path: str | None = None
    item_name: str | None = None
    expected: Any | None = None
    actual: Any | None = None
    source: Literal["parser", "normalizer", "validator", "approver", "payment", "system"]
    recoverable: bool = True


class ApprovalDecision(BaseModel):
    status: ApprovalStatus
    approved_by: str
    reasoning: str
    blocking_issue_codes: list[str] = Field(default_factory=list)
    requires_human_review: bool = False
    reflection_notes: list[str] = Field(default_factory=list)
    confidence: Decimal | None = None


class PaymentResult(BaseModel):
    status: PaymentStatus
    payment_id: str | None = None
    idempotency_key: str
    vendor_name: str | None = None
    amount: Decimal | None = None
    currency: str = "USD"
    error_message: str | None = None
    attempted_at: datetime


class WorkflowResult(BaseModel):
    run_id: UUID
    status: WorkflowStatus
    invoice: Invoice | None = None
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    approval_decision: ApprovalDecision | None = None
    payment_result: PaymentResult | None = None
    audit_log_path: str
    started_at: datetime
    completed_at: datetime
```

Supporting stage types should stay small and live next to the services that own them. They are included here so each stage contract has a concrete input and output name.

```python
class CliArgs(BaseModel):
    invoice_path: str
    db_path: str = "inventory.db"
    audit_path: str = "audit/events.jsonl"
    dry_run: bool = False
    max_retries: int = 2


class RuntimeConfig(BaseModel):
    db_path: str
    audit_path: str
    autonomous_approval_limit: Decimal = Decimal("10000")
    allow_reflection_fallback: bool = True
    max_retries: int = 2


class WorkflowRequest(BaseModel):
    invoice_path: str
    config: RuntimeConfig


class SourceDocument(BaseModel):
    path: str
    source_format: Literal["pdf", "txt", "json", "csv", "xml"]
    sha256: str
    size_bytes: int
    text_preview: str | None = None


class RawInvoicePayload(BaseModel):
    source_format: Literal["pdf", "txt", "json", "csv", "xml"]
    parser_name: str
    data: dict[str, Any]
    raw_text: str | None = None
    confidence: Decimal | None = None
    parser_warnings: list[str] = Field(default_factory=list)


class SetupRequest(BaseModel):
    db_path: str
    reset: bool = False
    seed_inventory: bool = True
```

## Stage Contracts

Every stage emits audit events with the common envelope below:

```python
AuditEvent = {
    "event_id": "uuid",
    "run_id": "uuid",
    "stage": "ingestion | parsing | normalization | validation | approval | reflection | payment | persistence | cli | tests",
    "event_type": "stage_started | stage_succeeded | stage_failed | stage_retried | stage_skipped",
    "timestamp": "iso-8601",
    "attempt": 1,
    "correlation_id": "invoice_number-or-source-fingerprint",
    "input_ref": {"path": "...", "format": "..."},
    "output_ref": {"model": "...", "record_id": "..."},
    "decision": {"gate": "...", "passed": True, "reason": "..."},
    "issues": [{"code": "...", "severity": "...", "message": "..."}],
    "duration_ms": 0,
}
```

Stage-specific payload keys are allowed under `metadata`, but the common envelope is required for every event.

| Stage | Input type | Output type | Deterministic gate condition | Retry behavior | Fail-fast behavior | Audit event shape |
|---|---|---|---|---|---|---|
| CLI | `CliArgs` with `invoice_path`, `db_path`, `audit_path`, `dry_run`, `max_retries` | `WorkflowResult` rendered to terminal and process exit code | Required paths/options parse; invoice path exists; db path is writable or setup can create it | No automatic retry; invalid user input exits with actionable message | Exit before orchestration when required CLI args or filesystem access fail | `stage=cli`, `event_type=stage_started/stage_failed/stage_succeeded`, `metadata.args`, `decision.gate=cli_args_valid` |
| Orchestration | `WorkflowRequest` containing source path and runtime config | `WorkflowResult` | Stages execute in declared order; each downstream stage receives the prior successful output; terminal status is one of the defined `WorkflowStatus` values | Retries only stages marked recoverable; max attempts comes from config; retry events include previous issue codes | Stop workflow on unrecoverable system errors, unsupported file type, invalid canonical invoice, or persistence failure for required records | `stage=orchestration`, `metadata.current_stage`, `metadata.next_stage`, `decision.gate=stage_transition_allowed` |
| Ingestion | `Path` plus runtime config | `SourceDocument` with path, extension, bytes/text handle, checksum, size | File exists, extension is supported, file is non-empty, checksum computed | Retry once for transient read errors | Fail fast on missing file, unsupported extension, empty file, permission error | `stage=ingestion`, `input_ref.path`, `metadata.file_size`, `metadata.sha256`, `decision.gate=source_readable` |
| Parsing adapters | `SourceDocument` | `AgentResult[RawInvoicePayload]` | Adapter returns syntactically valid payload with at least one candidate invoice field or blocking parser issue | Retry agent/OCR extraction up to configured max when adapter reports recoverable low-confidence parse; deterministic JSON/XML/CSV syntax errors are not retried | Fail fast on corrupt binary/PDF that cannot be opened, invalid JSON/XML syntax, or no adapter registered | `stage=parsing`, `metadata.adapter`, `metadata.source_format`, `metadata.confidence`, `decision.gate=raw_payload_extracted` |
| Normalization | `RawInvoicePayload` plus source metadata | `AgentResult[Invoice]` | Canonical `Invoice` model validates; money/date fields are typed; line items are represented as `LineItem`; source metadata is preserved | Retry agent-assisted field repair once when required canonical fields are missing but recoverable evidence exists in raw text | Fail fast when canonical model cannot be created, source path/format is lost, or zero line items are found after repair | `stage=normalization`, `output_ref.model=Invoice`, `metadata.field_map_version`, `decision.gate=canonical_invoice_valid` |
| Validation | `Invoice` plus `InventoryRepository` | `AgentResult[list[ValidationIssue]]` | All deterministic checks complete; blocking issues are explicitly classified; database lookup result exists for every line item attempt | Retry transient SQLite lock/connection errors; do not retry deterministic data failures such as negative quantity or unknown item | Fail fast on unavailable inventory database, schema mismatch, or validator configuration error | `stage=validation`, `metadata.inventory_db`, `metadata.issue_counts`, `decision.gate=validation_completed` |
| Approval | `Invoice` plus validation issues | `AgentResult[ApprovalDecision]` | Blocking validation issues force `rejected` or `needs_review`; approved decisions require no blocking issues and amount under configured autonomous threshold | Retry LLM/reflection response formatting failures; do not retry deterministic approval rules | Fail fast if approval decision is internally inconsistent, such as `approved` with blocking issues | `stage=approval`, `metadata.amount`, `metadata.thresholds`, `decision.gate=approval_policy_satisfied` |
| Reflection | Draft `ApprovalDecision`, `Invoice`, validation issues | `AgentResult[ApprovalDecision]` | Reflection can only tighten a decision: `approved` may become `needs_review`/`rejected`; rejected cannot become approved without deterministic gate re-check | Retry malformed reflection output once; if reflection client is unavailable, continue with rule-based decision and warning if config allows | Fail fast when reflection returns a decision that violates deterministic approval gate | `stage=reflection`, `metadata.original_status`, `metadata.final_status`, `decision.gate=reflection_consistent` |
| Payment | `Invoice` and final `ApprovalDecision` | `PaymentResult` | Payment called only when approval status is `approved`, no blocking issues exist, total is positive, vendor is known, and idempotency key is available | Retry mock payment on transient simulated failure up to config; same idempotency key must be reused | Skip payment for rejected/needs-review decisions; fail fast on approved invoice with missing amount/vendor/idempotency key | `stage=payment`, `metadata.idempotency_key`, `metadata.payment_status`, `decision.gate=payment_allowed` |
| Audit logging | `AuditEvent` envelope from any stage | Durable JSONL line and optional SQLite audit row | Event matches schema; timestamp/run_id/stage/event_type are present; write succeeds | Retry transient file write or SQLite lock errors; never retry by changing event contents | Fail fast if audit sink cannot be initialized at workflow start; during workflow, degrade to stderr only if explicitly configured | `stage=audit`, `metadata.sink=jsonl/sqlite/stderr`, `decision.gate=audit_event_persisted` |
| Persistence/setup | `SetupRequest`, domain records | Initialized SQLite schema and saved records | Required tables exist; inventory seed is idempotent; workflow/payment/invoice records can be written | Retry SQLite lock errors; setup is idempotent and safe to rerun | Fail fast on schema migration failure, unwritable db path, or incompatible schema version | `stage=persistence`, `metadata.schema_version`, `metadata.tables_checked`, `decision.gate=persistence_ready` |
| Tests | `pytest` invocation and fixture paths | Test report / coverage result | Unit and integration suites pass; sample invoices produce expected terminal workflow statuses | No runtime retry; flaky external clients are replaced with fakes | Fail fast in CI on first infrastructure/setup failure; otherwise collect all assertion failures | `stage=tests`, `metadata.test_suite`, `metadata.fixtures`, `decision.gate=quality_bar_met` |

## File and Module Layout

```text
galatiq-case-invoices/
  main.py
  pyproject.toml
  README.md
  docs/
    architecture.md
  data/
    invoices/
    generate_pdfs.py
  src/
    galatiq_invoices/
      __init__.py
      cli.py
      config.py
      domain/
        __init__.py
        models.py
        errors.py
        protocols.py
      orchestration/
        __init__.py
        workflow.py
        stages.py
        retry.py
      ingestion/
        __init__.py
        source_document.py
        registry.py
      parsing/
        __init__.py
        base.py
        json_adapter.py
        csv_adapter.py
        xml_adapter.py
        txt_adapter.py
        pdf_adapter.py
      normalization/
        __init__.py
        normalizer.py
        field_mapping.py
      validation/
        __init__.py
        validator.py
        rules.py
      approval/
        __init__.py
        approver.py
        reflection.py
        policies.py
      payment/
        __init__.py
        client.py
        mock_client.py
      audit/
        __init__.py
        logger.py
        events.py
        sinks.py
      persistence/
        __init__.py
        db.py
        migrations.py
        repositories.py
        seed.py
  tests/
    conftest.py
    unit/
      test_models.py
      test_parser_registry.py
      test_normalizer_contract.py
      test_validation_rules.py
      test_approval_policy.py
      test_payment_gate.py
      test_audit_events.py
    integration/
      test_workflow_clean_invoice.py
      test_workflow_invalid_quantity.py
      test_workflow_unknown_item.py
      test_workflow_rejected_payment_skip.py
      test_all_sample_invoices.py
    fixtures/
      expected_results.json
```

## Module Responsibilities

- `main.py`: thin executable wrapper that calls `galatiq_invoices.cli.app`.
- `cli.py`: Typer commands: `process`, `setup-db`, `validate-config`, and optionally `process-batch`.
- `config.py`: runtime settings, thresholds, retry counts, paths, feature flags.
- `domain/models.py`: typed contracts listed above.
- `domain/protocols.py`: interfaces for parser adapters, agent clients, validators, payment clients, repositories, and audit sinks.
- `orchestration/workflow.py`: owns run lifecycle and stage sequencing.
- `orchestration/stages.py`: typed stage wrappers that connect domain services without embedding business rules.
- `orchestration/retry.py`: retry policy objects and attempt accounting.
- `ingestion/source_document.py`: source file descriptor and checksum metadata.
- `ingestion/registry.py`: adapter lookup by extension.
- `parsing/*_adapter.py`: format-specific extraction only.
- `normalization/normalizer.py`: raw payload to `Invoice` conversion.
- `validation/rules.py`: deterministic validation rule definitions.
- `validation/validator.py`: applies rules and returns `ValidationIssue` objects.
- `approval/policies.py`: thresholds and deterministic approval constraints.
- `approval/approver.py`: draft approval decision.
- `approval/reflection.py`: critique loop constrained by deterministic policy.
- `payment/client.py`: payment protocol and shared request/response helpers.
- `payment/mock_client.py`: local mock implementation.
- `audit/events.py`: audit event model and helpers.
- `audit/logger.py`: stage-facing logger API.
- `audit/sinks.py`: JSONL, SQLite, stderr sinks.
- `persistence/db.py`: SQLite connection/session management.
- `persistence/migrations.py`: schema creation and version checks.
- `persistence/repositories.py`: inventory, workflow run, invoice, payment repositories.
- `persistence/seed.py`: idempotent inventory seed command.

## Deterministic Gates Summary

- No parser output proceeds unless it can be normalized into `Invoice`.
- No validation result is considered complete unless every line item has an inventory lookup attempt or a system-level database issue.
- No approval can be `approved` when any blocking validation issue exists.
- No reflection output can loosen deterministic approval policy.
- No payment can run unless the final decision is `approved`, the invoice total is positive, and vendor identity is present.
- No workflow can report `success` unless payment succeeded or the run is explicitly `dry_run`.
- No required persistence/audit sink can silently fail.

## Test Strategy

- Contract tests verify `pydantic` validation, enum values, and serialization stability.
- Adapter tests cover one fixture per format: JSON, CSV row-pair, CSV tabular, XML, TXT, PDF.
- Normalization tests verify field mapping, missing field issue creation, date parsing boundaries, and `Decimal` preservation.
- Validation tests cover in-stock, out-of-stock, unknown item, negative quantity, missing vendor, missing due date, and total mismatch.
- Approval tests cover under-threshold approval, over-threshold review, blocking issue rejection, and reflection tightening.
- Payment tests prove rejected invoices skip payment and approved invoices use stable idempotency keys.
- Audit tests snapshot event envelopes for every stage and assert required fields exist.
- Integration tests run sample invoices from `data/invoices` through the full workflow using fake parser/agent/payment dependencies where needed.

## Implementation Order

1. Create package skeleton and domain contracts.
2. Add persistence setup and inventory seed command.
3. Add audit event schema and JSONL sink.
4. Add ingestion registry and parser adapter interfaces.
5. Add deterministic normalizer and validator contracts.
6. Add approval policy/reflection interfaces.
7. Add mock payment client and payment gate.
8. Wire orchestration and CLI.
9. Add fixture-driven tests.
