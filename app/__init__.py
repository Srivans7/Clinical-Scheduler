"""
Flask application entry point for the Clinical Trial Scheduler.
"""
import os
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename

from app.audit_store import (
    get_recent_transactions,
    get_transaction_details,
    delete_transaction,
    register_schedule_run,
    register_upload,
)
from app.excel_parser import parse_excel
from app.scheduler import run_all_strategies, compute_utilization
from app.models import serial_to_iso, iso_to_serial, NewStudyInput

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "..", "uploads")
ALLOWED_EXTENSIONS = {"xlsx"}

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB limit


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _validate_new_study_payload(payload: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    normalized: dict = {}

    protocol = str(payload.get("protocol", "")).strip()
    if not protocol:
        errors.append("Protocol is required.")
    normalized["protocol"] = protocol

    preferred_checkin = str(payload.get("preferred_checkin", "")).strip()
    if not preferred_checkin:
        errors.append("Preferred check-in date is required.")
    else:
        try:
            iso_to_serial(preferred_checkin)
        except ValueError:
            errors.append("Preferred check-in must be a valid ISO date.")
    normalized["preferred_checkin"] = preferred_checkin

    numeric_fields = {
        "male": (0, "Male participants"),
        "female": (0, "Female participants"),
        "periods": (1, "Number of periods"),
        "washout": (0, "Washout days"),
        "los": (1, "Length of stay"),
    }

    for field_name, (minimum, label) in numeric_fields.items():
        raw_value = payload.get(field_name)
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            errors.append(f"{label} must be a whole number.")
            continue
        if value < minimum:
            comparator = "at least"
            errors.append(f"{label} must be {comparator} {minimum}.")
        normalized[field_name] = value

    if normalized.get("male", 0) + normalized.get("female", 0) <= 0:
        errors.append("At least one participant is required.")

    return normalized, errors


def _build_results_summary(results: list[dict]) -> dict:
    feasible = [item for item in results if item.get("feasible")]
    # Prefer the earliest feasible check-in (minimum shift) while keeping
    # original strategy order as a stable tie-breaker.
    recommended = min(feasible, key=lambda item: int(item.get("shift_days", 0))) if feasible else None
    return {
        "total_strategies": len(results),
        "feasible_count": len(feasible),
        "recommended_strategy": recommended.get("strategy") if recommended else None,
        "recommended_note": recommended.get("note") if recommended else None,
        "recommended_shift_days": recommended.get("shift_days", 0) if recommended else None,
        "recommended_period_count": len(recommended.get("periods", [])) if recommended else 0,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/history")
def history_page():
    return render_template("history.html")


@app.route("/utilization")
def utilization_page():
    return render_template("utilization.html")


@app.route("/api/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok"}), 200


@app.errorhandler(413)
def payload_too_large(_exc):
    return jsonify({"error": "File is too large. Maximum allowed size is 10 MB."}), 413


@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400
    if not allowed_file(file.filename):
        return jsonify({"error": "Only .xlsx files are supported"}), 400

    filename = secure_filename(file.filename)
    upload_dir = app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, filename)
    try:
        file.save(filepath)
    except Exception as exc:
        return jsonify({"error": f"Failed to save uploaded file: {exc}"}), 500

    try:
        clinics, existing_periods, new_study = parse_excel(filepath)
    except Exception as exc:
        return jsonify({"error": f"Failed to parse Excel: {exc}"}), 500

    clinics_data = [{"id": c.clinic_id, "capacity": c.capacity} for c in clinics]

    existing_data = []
    for p in existing_periods:
        existing_data.append({
            "protocol": p.protocol,
            "period": p.period_label,
            "male": p.male_count,
            "female": p.female_count,
            "male_clinic": p.male_clinic,
            "female_clinic": p.female_clinic,
            "checkin": serial_to_iso(p.checkin_serial) if p.checkin_serial else "",
            "checkout": serial_to_iso(p.checkout_serial) if p.checkout_serial else "",
            "los": p.los,
        })

    new_study_data = None
    if new_study:
        new_study_data = {
            "protocol": new_study.protocol,
            "male": new_study.male_count,
            "female": new_study.female_count,
            "periods": new_study.periods,
            "washout": new_study.washout_days,
            "los": new_study.los,
            "preferred_checkin": serial_to_iso(new_study.preferred_checkin_serial),
        }

    audit = register_upload(
        original_filename=file.filename,
        stored_filename=filename,
        file_path=filepath,
        parsed_input=new_study_data,
        clinics=clinics_data,
        existing_schedule=existing_data,
    )

    return jsonify({
        "filename": filename,
        "clinics": clinics_data,
        "existing_schedule": existing_data,
        "new_study": new_study_data,
        "audit": audit,
    })


def _load_schedule_context(data: dict):
    if not data:
        raise ValueError("No JSON data provided")

    transaction_id = str(data.get("transaction_id", "")).strip()
    if not transaction_id:
        raise ValueError("Missing transaction id. Please upload the file again.")

    filepath = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(data.get("filename", "")))
    if not os.path.isfile(filepath):
        raise FileNotFoundError("File not found. Please upload again.")

    try:
        clinics, existing_periods, _ = parse_excel(filepath)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse Excel: {exc}") from exc

    normalized, errors = _validate_new_study_payload(data.get("new_study") or {})
    new_study = None
    if not errors:
        new_study = NewStudyInput(
            protocol=normalized["protocol"],
            male_count=normalized["male"],
            female_count=normalized["female"],
            periods=normalized["periods"],
            washout_days=normalized["washout"],
            los=normalized["los"],
            preferred_checkin_serial=iso_to_serial(normalized["preferred_checkin"]),
        )

    return transaction_id, normalized, errors, new_study, clinics, existing_periods


@app.route("/api/schedule-preview", methods=["POST"])
def schedule_preview():
    try:
        transaction_id, normalized, errors, new_study, clinics, existing_periods = _load_schedule_context(request.get_json(force=True))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500

    if errors:
        return jsonify({"error": "Invalid new study parameters.", "errors": errors}), 400

    results = run_all_strategies(new_study, clinics, existing_periods)
    return jsonify({
        "transaction_id": transaction_id,
        "preview_input": normalized,
        "results": results,
        "summary": _build_results_summary(results),
    })


@app.route("/api/schedule-confirm", methods=["POST"])
def schedule_confirm():
    try:
        transaction_id, normalized, errors, new_study, clinics, existing_periods = _load_schedule_context(request.get_json(force=True))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500

    if errors:
        return jsonify({"error": "Invalid new study parameters.", "errors": errors}), 400

    results = run_all_strategies(new_study, clinics, existing_periods)
    audit = register_schedule_run(
        transaction_id=transaction_id,
        request_input=normalized,
        results=results,
    )
    return jsonify({
        "results": results,
        "summary": _build_results_summary(results),
        "audit": audit,
    })


@app.route("/api/schedule", methods=["POST"])
def schedule():
    return schedule_confirm()


@app.route("/api/audit-log", methods=["GET"])
def audit_log():
    try:
        limit = int(request.args.get("limit", "8"))
    except ValueError:
        limit = 8
    limit = max(1, min(limit, 50))
    query = str(request.args.get("q", "")).strip()
    status = str(request.args.get("status", "")).strip()
    return jsonify({"items": get_recent_transactions(limit=limit, query=query, status=status)})


@app.route("/api/audit-log/<transaction_id>", methods=["GET"])
def audit_log_detail(transaction_id: str):
    details = get_transaction_details(transaction_id)
    if not details:
        return jsonify({"error": "Transaction not found."}), 404
    return jsonify(details)


@app.route("/api/audit-log/<transaction_id>", methods=["DELETE"])
def audit_log_delete(transaction_id: str):
    deleted = delete_transaction(transaction_id)
    if not deleted.get("deleted"):
        return jsonify({"error": "Transaction not found."}), 404
    return jsonify(deleted)


@app.route("/api/utilization")
def utilization_api():
    """Return a date × clinic bed-occupancy grid for a given transaction."""
    transaction_id = request.args.get("transaction_id", "").strip()
    if not transaction_id:
        return jsonify({"error": "transaction_id query parameter is required."}), 400

    details = get_transaction_details(transaction_id)
    if not details:
        return jsonify({"error": "Transaction not found."}), 404

    file_row = details.get("file")
    if not file_row:
        return jsonify({"error": "File record not found."}), 404

    filepath = file_row.get("file_path", "")
    if not os.path.isfile(filepath):
        # Fallback for migrated/legacy records whose original absolute file paths
        # no longer exist on the current host (e.g., local Windows path on server).
        stored_filename = secure_filename(file_row.get("stored_filename", ""))
        candidate = os.path.join(app.config["UPLOAD_FOLDER"], stored_filename) if stored_filename else ""
        if candidate and os.path.isfile(candidate):
            filepath = candidate
        else:
            return jsonify({"error": "Uploaded file is no longer available on disk."}), 404

    try:
        clinics, existing_periods, _ = parse_excel(filepath)
    except Exception as exc:
        return jsonify({"error": f"Failed to parse file: {exc}"}), 500

    # Collect the recommended strategy's periods from the latest confirmed
    # output. Recommendation prefers the minimum shift among feasible results.
    new_study_periods: list[dict] = []
    for out in details.get("outputs", []):
        feasible_results = [item for item in (out.get("results") or []) if item.get("feasible")]
        if feasible_results:
            recommended_result = min(feasible_results, key=lambda item: int(item.get("shift_days", 0)))
            new_study_periods = recommended_result.get("periods", [])
        if new_study_periods:
            break

    grid_data = compute_utilization(clinics, existing_periods, new_study_periods or None)
    grid_data["transaction_id"] = transaction_id
    grid_data["filename"] = file_row.get("original_filename", "")
    return jsonify(grid_data)


if __name__ == "__main__":
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    app.run(debug=True, port=5000)
