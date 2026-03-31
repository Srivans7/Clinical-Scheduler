# Clinical Trial Scheduler: System Analysis, Vision, and Problem Statement

## 1. Executive Summary

Clinical Trial Scheduler is a decision-support web application for clinical operations teams that need to fit a new study into a constrained, already-booked clinic calendar.

The system ingests structured planning data from Excel, evaluates three scheduling strategies under real operational constraints, presents a preview-first planning workflow, and persists every confirmed action in a linked audit trail in SQLite.

This project solves a high-friction planning problem where manual scheduling in spreadsheets is slow, error-prone, and difficult to audit.

## 2. Problem Being Solved

### 2.1 Core Operational Problem

Clinical teams must place a new study into a shared clinic infrastructure where:

- Existing studies already occupy beds and date windows.
- Male/female allocations have separation constraints.
- Clinics can run only a limited number of distinct studies simultaneously.
- Study periods require length-of-stay and washout spacing.

Manually finding a feasible placement is hard because constraints interact across time, capacity, and study policy.

### 2.2 Pain Points in Manual Process

Without a system like this:

- Teams spend significant time testing candidate dates and allocations manually.
- Different planners can reach different decisions for the same data.
- Constraints are easy to violate accidentally.
- It is difficult to explain why a schedule was chosen.
- Auditability is weak: uploads, user edits, preview outcomes, and final outputs are not cleanly linked.

### 2.3 Business Risk if Unsolved

- Delayed trial starts due to planning bottlenecks.
- Higher risk of capacity conflicts and rework.
- Reduced confidence during internal/external review.
- Inability to trace who processed which input into which output.

## 3. Vision of the Project

## 3.1 Product Vision

Build a trustworthy, auditable scheduling copilot for clinical operations that transforms static spreadsheet planning into a deterministic, explainable, and reviewable workflow.

## 3.2 Experience Vision

The desired user experience is:

1. Upload case-study data.
2. Review and edit proposed study inputs.
3. Preview strategy outcomes before committing.
4. Confirm and store final output only after validation.
5. Retrieve complete transaction history anytime with full linkage.

This creates confidence, transparency, and operational control.

## 3.3 Data Vision

Every business event should be traceable as a linked chain:

- Transaction (`TRN-*`)
- File (`FIL-*`)
- Source input record from upload parse (`INP-*`)
- Reviewed request input record at confirm time (`INP-*`)
- Output schedule record (`OUT-*`)
- Operations (`OP-*`) for upload and confirm actions

This is the backbone for governance and compliance readiness.

## 4. What the Current System Does

## 4.1 Functional Workflow

### Step A: Upload and Parse

- Accepts `.xlsx` file uploads (max 10 MB).
- Parses Clinics, Existing Schedule, and New Study sections.
- Creates upload-linked audit records in SQLite.

### Step B: Review and Validate

- Shows editable new study inputs.
- Performs validation (required fields, integer checks, minimum constraints, participant count).
- Shows before/after change preview against uploaded defaults.

### Step C: Preview Scheduling

- Runs all three strategies without final persistence.
- Returns feasibility, shift days, period-wise allocations, and summary.
- Requires re-preview if user edits inputs after preview.

### Step D: Confirm and Store

- Re-runs scheduling with validated input.
- Persists confirmed request input + output + operation links.
- Exposes full linked IDs in UI.

### Step E: History and Traceability

- Dedicated History page supports search/filter.
- API provides list and per-transaction detail views.
- Allows retrospective inspection of linked records.

## 4.2 Scheduling Strategies Implemented

The engine evaluates:

1. `Shift`: shift date forward, keep participants unsplit if possible.
2. `Split`: keep preferred date, allow split across up to two clinics per gender.
3. `Shift+Split`: combine date shifting and split flexibility.

## 4.3 Constraints Enforced

- C1: Males and females cannot share a clinic allocation.
- C2: A clinic cannot run more than two distinct studies simultaneously.
- C3: A study side (male/female) cannot be split across more than two clinics.
- Additional temporal logic: LOS and washout across periods.

## 4.4 Complete Feature Inventory (Implemented)

### Core Scheduling Features

- Multi-strategy scheduling engine (`Shift`, `Split`, `Shift+Split`).
- Feasibility evaluation with ranked recommendation (first feasible strategy).
- Date shifting search window (`MAX_SHIFT_DAYS = 120`).
- Single-clinic and two-clinic split allocation logic.
- Period-by-period schedule generation with LOS and washout handling.
- Per-period output with check-in/check-out dates and male/female clinic maps.

### Constraint and Validation Features

- Gender separation constraint (male/female cannot share clinic).
- Clinic concurrency guard (max two distinct studies per clinic/day).
- Split limit constraint (max two clinics per study side).
- Input schema validation for protocol, date, counts, LOS, washout, periods.
- Integer and minimum-bound validation for numeric fields.
- Participant total validation (at least one participant required).
- Upload validation for file type (`.xlsx`) and file size (10 MB).

### Data Ingestion and Parsing Features

- `.xlsx` parsing without `openpyxl` using stdlib (`zipfile` + XML).
- Shared string decoding from `sharedStrings.xml`.
- Dynamic sheet resolution from workbook relationship metadata.
- Parsing support for sheets: `Clinics`, `Existing Sch`, `New Study`.
- Parsing of clinic-allocation strings like `1 (50) + 2 (14)` into maps.
- Excel serial date conversion utilities (serial <-> ISO date).

### API Features

- `POST /api/upload` for ingestion and parsed data return.
- `POST /api/schedule-preview` for non-persistent preview computation.
- `POST /api/schedule-confirm` for persistent confirmed scheduling.
- `POST /api/schedule` compatibility alias mapped to confirm flow.
- `GET /api/audit-log` with limit, status, and free-text query filters.
- `GET /api/audit-log/<transaction_id>` for detailed trace retrieval.
- JSON error responses with specific validation messages.

### Audit, Traceability, and Persistence Features

- SQLite audit store (`data/audit_store.db`) as primary persistence.
- Legacy JSON migration path (`data/audit_store.json` -> SQLite).
- Deterministic business IDs with counters:
- `TRN-*` transaction IDs.
- `FIL-*` file IDs.
- `INP-*` input record IDs.
- `OUT-*` output record IDs.
- `OP-*` operation IDs.
- Linked record chain from upload to confirmed output.
- File fingerprinting via SHA-256.
- Stored metadata: original filename, stored filename, path, size, timestamps.
- Stored payload snapshots for upload-parsed input and confirmed request input.
- Stored schedule summaries and full strategy output payloads.

### Planner UI Features

- Drag-and-drop upload zone with browse fallback.
- Upload status feedback (loading/success/error).
- Step tracker (`Upload` -> `Review` -> `Results`).
- Clinics capacity cards and visual capacity bars.
- Existing schedule table with protocol/period/date formatting.
- Editable study parameter form with field-level error highlighting.
- Validation summary panel for errors/warnings/ready state.
- Change preview panel (`Uploaded` vs `Current`) with change counter.
- Review metadata chips showing linked IDs and storage mode.
- Preview-first workflow (`Preview Schedule` action).
- Confirm-gated persistence workflow (`Confirm & Store` action).
- Stale-preview detection when inputs change after preview.
- Strategy cards with feasibility badges and expandable details.
- Period allocation chips for male/female clinic assignments.
- Final summary panel (recommendation, feasibility, shift, mode).
- Linked audit summary panel (transaction/file/input/output/ops IDs).

### History UI Features

- Dedicated history page at `/history`.
- Search box for transaction/file/output/filename queries.
- Status filter (`uploaded`, `scheduled`).
- Refresh action and record count indicator.
- KPI-style history stats (transactions, scheduled, uploaded-only, unique files).
- Transaction cards with summary and timeline information.
- Detail hydration from per-transaction audit-detail API.
- Graceful fallback cards if detail fetch fails.

### UX and Design Features

- Unified design system with reusable tokens (color, spacing, radius, shadow).
- Responsive layouts for desktop and mobile breakpoints.
- Navigation links between planner and history pages.
- Structured panels for upload, review, results, and history exploration.
- Animated reveal states (`fade-up`) and async action spinners.

### Reliability and Safety Features

- Atomic SQLite writes through transaction boundaries.
- Thread lock around audit-store operations.
- Defensive parsing and error handling for malformed inputs.
- Compatibility endpoint retained to avoid integration breakages.
- Backend smoke-tested with real-case Excel payload through Flask test client.

## 5. Architecture Analysis

## 5.1 Backend

- Framework: Flask
- Core modules:
  - `app/__init__.py`: routes, validation, API orchestration
  - `app/excel_parser.py`: stdlib-based XLSX parsing (zip+xml)
  - `app/scheduler.py`: strategy engine + constraint logic
  - `app/audit_store.py`: SQLite persistence + legacy JSON migration
  - `app/models.py`: dataclasses and date conversions

## 5.2 Frontend

- Server-rendered HTML templates + vanilla JS
- Main planner page with preview-confirm workflow
- History page for transaction exploration
- Single design system in CSS

## 5.3 Persistence

- Primary store: `data/audit_store.db` (SQLite)
- Legacy compatibility: reads and migrates `data/audit_store.json`
- Deterministic ID generation via counters for business records

## 5.4 API Surface

- `POST /api/upload`
- `POST /api/schedule-preview`
- `POST /api/schedule-confirm`
- `POST /api/schedule` (compatibility alias to confirm)
- `GET /api/audit-log`
- `GET /api/audit-log/<transaction_id>`

## 6. Why This Project Matters

This system is not just a scheduler; it is a planning governance layer.

It provides:

- Speed: faster feasibility checks than manual spreadsheet iteration
- Consistency: deterministic strategy evaluation
- Safety: validation and constraint enforcement
- Transparency: preview-before-commit workflow
- Auditability: linked records from upload to final output

For clinical operations teams, this directly reduces planning turnaround time and improves confidence in scheduling decisions.

## 7. Current Strengths

- Clear separation between parsing, scheduling, API, and persistence.
- Strong operational constraints modeled in code.
- Preview/confirm workflow prevents accidental finalization.
- Linked audit chain with human-readable IDs.
- Searchable history endpoint and UI page.
- SQLite migration path from earlier JSON store.

## 8. Known Gaps and Future Evolution

## 8.1 Functional Gaps

- No role-based access control or user identity tracking.
- No explicit optimistic locking/version conflict handling for concurrent users.
- No export package (CSV/PDF) for confirmation artifacts.

## 8.2 Technical Gaps

- No formal test suite yet (unit + integration + contract tests).
- No background job queue for large/batch scheduling workloads.
- No environment-specific config layer for production deployment.

## 8.3 Product Roadmap Candidates

1. Multi-user authentication and action attribution.
2. Scenario comparison (save multiple previews before confirm).
3. Explainability panel showing why each strategy failed/passed by constraint.
4. Export and reporting module for regulatory-ready artifacts.
5. Notifications/webhooks on confirmed schedule events.

## 9. Success Metrics (Suggested)

To evaluate project impact:

- Time to generate feasible plan (manual vs system-assisted)
- Percentage of first-pass feasible schedules
- Reduction in post-planning conflicts/rework
- Number of transactions with complete linked audit chain
- User adoption and repeat usage rate

## 10. Conclusion

Clinical Trial Scheduler solves a concrete operational bottleneck: placing new studies into constrained clinic capacity while preserving policy and traceability.

The current implementation already delivers a strong foundation:

- constrained scheduling engine,
- preview-before-store workflow,
- and linked SQLite audit history.

The project vision is to become a trusted planning command center for clinical operations, where every scheduling decision is both feasible and explainable.
