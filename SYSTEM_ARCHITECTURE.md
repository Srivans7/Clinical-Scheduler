# System Architecture — Clinical Trial Scheduler

---

## 1. High-Level Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Browser (Client)                           │
│                                                                     │
│  index.html + app.js          history.html + history.js             │
│  ┌───────────────────┐        ┌──────────────────────┐              │
│  │  Planner UI        │        │  History Explorer UI  │              │
│  │  Upload → Review   │        │  Filter / Detail view │              │
│  │  Preview → Confirm │        └──────────────────────┘              │
│  └────────┬──────────┘                  │                            │
│           │  REST (JSON / multipart)    │  REST (JSON)               │
└───────────┼─────────────────────────────┼────────────────────────────┘
            │                             │
┌───────────▼─────────────────────────────▼────────────────────────────┐
│                        Flask Application                              │
│                         app/__init__.py                               │
│                                                                       │
│  POST /api/upload          →  upload_file()                           │
│  POST /api/schedule-preview →  schedule_preview()                     │
│  POST /api/schedule-confirm →  schedule_confirm()                     │
│  POST /api/schedule        →  (compatibility alias)                   │
│  GET  /api/audit-log       →  audit_log_list()                        │
│  GET  /api/audit-log/<id>  →  audit_log_detail()                      │
│  GET  /                    →  index page                              │
│  GET  /history             →  history page                            │
└───────┬──────────────┬──────────────────────┬─────────────────────────┘
        │              │                      │
        ▼              ▼                      ▼
┌──────────────┐ ┌───────────────┐  ┌─────────────────────┐
│ excel_parser │ │   scheduler   │  │    audit_store      │
│              │ │               │  │                     │
│ Reads .xlsx  │ │ Strategy 1:   │  │  SQLite DB          │
│ via stdlib   │ │   Shift       │  │  (audit_store.db)   │
│ XML/zip      │ │ Strategy 2:   │  │                     │
│              │ │   Split       │  │  Tables:            │
│ Sheets:      │ │ Strategy 3:   │  │  - transactions     │
│  Clinics     │ │   Shift+Split │  │  - inputs           │
│  New Study   │ │               │  │  - outputs          │
│  Existing Sch│ │ Constraints:  │  │  - operations       │
│  Strategies  │ │  C1 C2 C3     │  │                     │
└──────────────┘ └───────────────┘  └─────────────────────┘
        │                │
        └────────┬───────┘
                 ▼
         ┌──────────────┐
         │    models    │
         │              │
         │  Clinic      │
         │  StudyPeriod │
         │  NewStudy    │
         │  Input       │
         └──────────────┘
```

---

## 2. Layer Breakdown

### 2.1 Frontend (Browser)

| File | Role |
|------|------|
| `app/templates/index.html` | Planner page — upload, review, preview, confirm |
| `app/templates/history.html` | History explorer page |
| `app/static/js/app.js` | Upload → validate → preview → confirm workflow |
| `app/static/js/history.js` | Audit list filtering and detail rendering |
| `app/static/css/style.css` | Full custom design system (no CSS framework) |

**Key frontend flow:**
```
User selects file
      │
attachFile()  ──► validates .xlsx extension, stores in state
      │
uploadFile()  ──► POST /api/upload  ──► fills form with parsed study data
      │
onFormChanged()  ──► live validation, marks preview as stale if edited
      │
previewSchedule()  ──► POST /api/schedule-preview  ──► shows diff panel
      │
confirmSchedule()  ──► POST /api/schedule-confirm  ──► saves to DB, shows final summary
```

---

### 2.2 API Layer (`app/__init__.py`)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/upload` | POST | Save file, parse Excel, register upload audit record |
| `/api/schedule-preview` | POST | Run all 3 strategies, return results (no DB write) |
| `/api/schedule-confirm` | POST | Run strategies + write confirmed output to SQLite |
| `/api/schedule` | POST | Compatibility alias for legacy calls |
| `/api/audit-log` | GET | List recent transactions |
| `/api/audit-log/<id>` | GET | Full detail of a single transaction |
| `/` | GET | Serve planner page |
| `/history` | GET | Serve history page |

**Validation** is done in `_validate_new_study_payload()` before any scheduling.  
**File handling** uses `secure_filename` + `os.makedirs(exist_ok=True)` to safely store uploads.

---

### 2.3 Excel Parser (`app/excel_parser.py`)

- Uses Python **stdlib** (`zipfile` + `xml.etree.ElementTree`) — no openpyxl dependency at runtime.
- Reads 4 sheets: `Clinics`, `New Study`, `Existing Sch`, `Strategies`.
- Parses clinic allocation strings like `"4 (14) + 5B (8)"` via regex into `{clinic_id: count}` maps.
- Returns typed domain objects: `List[Clinic]`, `List[StudyPeriod]`, `Optional[NewStudyInput]`.

---

### 2.4 Scheduler Engine (`app/scheduler.py`)

Three strategies run in sequence via `run_all_strategies()`:

```
Strategy 1 — Shift
  Keep participants together (no split).
  Scan forward from preferred check-in day by day until a
  single clinic has enough free capacity.

Strategy 2 — Split
  Stay at preferred check-in date.
  If no single clinic fits, split across ≤ 2 clinics.

Strategy 3 — Shift + Split
  Scan forward from preferred check-in.
  At each candidate date, allow splitting across ≤ 2 clinics.
```

**Constraints enforced on every period:**

| ID | Rule |
|----|------|
| C1 | Males and females must not share the same clinic |
| C2 | A clinic may hold at most 2 distinct studies simultaneously |
| C3 | A study's gender group may be split across at most 2 clinics |

---

### 2.5 Domain Models (`app/models.py`)

| Class | Fields |
|-------|--------|
| `Clinic` | `clinic_id`, `capacity` |
| `StudyPeriod` | protocol, period label, male/female counts, clinic maps, check-in/out serials, LOS |
| `NewStudyInput` | protocol, male/female counts, periods, washout days, LOS, preferred check-in serial |

Date handling uses **Excel serial numbers** internally (days since 1900-01-00) converted via `serial_to_iso` / `iso_to_serial`.

---

### 2.6 Audit Store (`app/audit_store.py`)

Persistent SQLite database at `data/audit_store.db`.

**Schema:**

```
transactions
  id          TEXT PK   (TRN-0001, TRN-0002, …)
  timestamp   TEXT
  status      TEXT      (uploaded | scheduled)
  original_filename TEXT

inputs
  id          TEXT PK   (INP-0001, …)
  transaction_id → transactions.id
  file_id     TEXT      (FIL-0001, …)
  payload     JSON

outputs
  id          TEXT PK   (OUT-0001, …)
  transaction_id → transactions.id
  payload     JSON

operations
  id          TEXT PK   (OP-0001, …)
  transaction_id → transactions.id
  input_id    → inputs.id
  output_id   → outputs.id
  timestamp   TEXT
  summary     JSON
```

**Linked ID chain:**  
`TRN (transaction) → FIL (file) → INP (input) → OUT (output) → OP (operation)`

---

## 3. Data Flow (End-to-End)

```
1. User uploads .xlsx
        │
        ▼
2. Flask saves file to /uploads/
   excel_parser reads Clinics + Existing Sch + New Study
        │
        ▼
3. audit_store.register_upload()  →  writes TRN + INP records to SQLite
        │
        ▼
4. Response: parsed clinic list, existing schedule, pre-filled study form
        │
        ▼
5. User edits study parameters → frontend validates locally
        │
        ▼
6. POST /api/schedule-preview
   scheduler.run_all_strategies()  →  returns 3 strategy results (no DB write)
        │
        ▼
7. Frontend renders diff / preview panel
        │
        ▼
8. User clicks Confirm & Store
        │
        ▼
9. POST /api/schedule-confirm
   scheduler.run_all_strategies()  →  runs again
   audit_store.register_schedule_run()  →  writes OUT + OP records to SQLite
        │
        ▼
10. Final summary shown; transaction visible on /history
```

---

## 4. Directory Structure

```
clinical-scheduler/
│
├── run.py                        Entry point (Flask dev server)
├── requirements.txt              Flask, Werkzeug
├── requirements-dev.txt          pytest
│
├── uploads/                      Uploaded .xlsx files (runtime)
├── data/
│   └── audit_store.db            SQLite audit database (runtime)
│
├── app/
│   ├── __init__.py               Flask app + all API routes
│   ├── models.py                 Domain dataclasses + date helpers
│   ├── excel_parser.py           .xlsx reader (stdlib XML/zip)
│   ├── scheduler.py              Scheduling engine (3 strategies + constraints)
│   ├── audit_store.py            SQLite persistence + migration helpers
│   │
│   ├── templates/
│   │   ├── index.html            Planner UI
│   │   └── history.html          History explorer UI
│   │
│   └── static/
│       ├── css/style.css         Custom design system
│       └── js/
│           ├── app.js            Planner workflow logic
│           └── history.js        History list + detail logic
│
└── tests/
    └── test_advanced_system.py   116-test pytest suite
```

---

## 5. Technology Choices

| Concern | Choice | Reason |
|---------|--------|--------|
| Web framework | Flask | Lightweight, minimal overhead for API-first app |
| Excel parsing | stdlib `zipfile` + `xml.etree` | No external runtime dependency |
| Persistence | SQLite (`sqlite3`) | Zero-config, file-based, portable |
| Frontend | Vanilla JS + custom CSS | No framework needed for this scale |
| Testing | pytest | Parametrized, clean fixtures, no extra runner needed |
| Scheduling | Rule-based engine | Deterministic, auditable, domain-accurate |
