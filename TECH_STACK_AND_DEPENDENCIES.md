# Clinical Trial Scheduler - Tech Stack, Frameworks, Libraries, Tools, and Dependencies

## 1) Core Tech Stack

- Backend language: Python 3
- Frontend: HTML5, CSS3, Vanilla JavaScript (no frontend framework)
- Web framework: Flask
- Templating: Jinja2 (via Flask)
- API style: JSON over HTTP (Flask routes)
- Database: SQLite (file-based, local)
- Data input format: Microsoft Excel `.xlsx`
- Test stack: Pytest

## 2) Frameworks Used

- Flask
  - Purpose: web app server, routing, request handling, JSON responses, template rendering
- Pytest
  - Purpose: automated testing for parser, scheduler constraints, API behavior, and persistence linkage

## 3) Python Libraries Used in Code

### Third-party libraries

- `flask`
  - Used for `Flask`, `request`, `jsonify`, `render_template`
- `werkzeug`
  - Used for `secure_filename`
- `pytest`
  - Used in test suite (`tests/test_advanced_system.py`)

### Standard library modules (directly used)

- `os`
- `io`
- `re`
- `json`
- `hashlib`
- `sqlite3`
- `threading`
- `datetime`
- `zipfile`
- `xml.etree.ElementTree`
- `pathlib`
- `typing`
- `dataclasses`

## 4) Frontend Libraries / Assets

- No frontend JS framework (no React/Vue/Angular)
- No CSS framework (no Bootstrap/Tailwind)
- External asset:
  - Google Fonts CDN for `Inter` font family

## 5) Persistence and Data Layer

- Primary store: SQLite database file
  - `data/audit_store.db`
- Legacy migration source:
  - `data/audit_store.json`
- Data access approach:
  - Python `sqlite3` with explicit SQL schema and statements

## 6) Declared Project Dependencies

From `requirements.txt`:

- `Flask>=3.0`
- `openpyxl>=3.1`
- `Werkzeug>=3.0`

From `requirements-dev.txt`:

- `pytest>=9.0`

## 7) Important Dependency Notes

- `openpyxl` is declared in `requirements.txt`, but current Excel parsing implementation uses stdlib (`zipfile` + XML) and does not require `openpyxl` at runtime for current code paths.
- `Werkzeug` is both a Flask dependency and directly referenced in app code (`secure_filename`).
- SQLite is part of Python stdlib (`sqlite3`) and does not require a separate pip package.

## 8) Tooling Used During Development/Testing

- Python virtual environment:
  - `C:/Users/Lenovo/Downloads/.stt/Scripts/python.exe`
- Package installer: `pip`
- Test runner: `pytest`
- Shell/automation: PowerShell
- IDE/workflow: VS Code + GitHub Copilot

## 9) Application Structure (Relevant to Stack)

- App entry and routes: `app/__init__.py`
- Excel parser: `app/excel_parser.py`
- Scheduling engine: `app/scheduler.py`
- Data models and date utils: `app/models.py`
- Audit persistence (SQLite): `app/audit_store.py`
- Planner UI template: `app/templates/index.html`
- History UI template: `app/templates/history.html`
- Planner JS: `app/static/js/app.js`
- History JS: `app/static/js/history.js`
- Styling system: `app/static/css/style.css`
- Advanced tests: `tests/test_advanced_system.py`

## 10) Execution and Test Commands

- Run tests:
  - `C:/Users/Lenovo/Downloads/.stt/Scripts/python.exe -m pytest -q`
- Run app (example):
  - `C:/Users/Lenovo/Downloads/.stt/Scripts/python.exe run.py`

