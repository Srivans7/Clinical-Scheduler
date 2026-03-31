"""
SQLite-backed audit store for uploaded files, reviewed inputs, and confirmed schedule outputs.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DB_PATH = os.path.join(DATA_DIR, "audit_store.db")
LEGACY_JSON_PATH = os.path.join(DATA_DIR, "audit_store.json")
_LOCK = threading.Lock()


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _sha256(file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS counters (
            name TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            upload_operation_id TEXT NOT NULL,
            source_input_record_id TEXT NOT NULL,
            latest_request_input_id TEXT,
            latest_output_record_id TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS files (
            file_id TEXT PRIMARY KEY,
            transaction_id TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS inputs (
            input_record_id TEXT PRIMARY KEY,
            transaction_id TEXT NOT NULL,
            file_id TEXT NOT NULL,
            source TEXT NOT NULL,
            payload_json TEXT,
            counts_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS outputs (
            output_record_id TEXT PRIMARY KEY,
            transaction_id TEXT NOT NULL,
            file_id TEXT NOT NULL,
            request_input_record_id TEXT NOT NULL,
            results_summary_json TEXT NOT NULL,
            results_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS operations (
            operation_id TEXT PRIMARY KEY,
            transaction_id TEXT NOT NULL,
            type TEXT NOT NULL,
            file_id TEXT NOT NULL,
            source_input_record_id TEXT,
            request_input_record_id TEXT,
            output_record_id TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS metadata (
            meta_key TEXT PRIMARY KEY,
            meta_value TEXT NOT NULL
        );
        """
    )

    for counter_name in ("transaction", "operation", "file", "input", "output"):
        conn.execute(
            "INSERT OR IGNORE INTO counters(name, value) VALUES (?, 0)",
            (counter_name,),
        )
    conn.execute(
        "INSERT OR IGNORE INTO metadata(meta_key, meta_value) VALUES (?, ?)",
        ("legacy_json_migrated", "0"),
    )
    conn.commit()


def _extract_counter_value(identifier: str) -> int:
    try:
        return int(str(identifier).rsplit("-", 1)[1])
    except (IndexError, ValueError, TypeError):
        return 0


def _set_counter_floor(conn: sqlite3.Connection, name: str, identifier: Optional[str]) -> None:
    if not identifier:
        return
    conn.execute(
        "UPDATE counters SET value = MAX(value, ?) WHERE name = ?",
        (_extract_counter_value(identifier), name),
    )


def _migrate_legacy_json(conn: sqlite3.Connection) -> None:
    migrated_row = conn.execute(
        "SELECT meta_value FROM metadata WHERE meta_key = ?",
        ("legacy_json_migrated",),
    ).fetchone()
    if migrated_row and str(migrated_row["meta_value"]) == "1":
        return

    has_transactions = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    if has_transactions:
        conn.execute(
            "UPDATE metadata SET meta_value = '1' WHERE meta_key = ?",
            ("legacy_json_migrated",),
        )
        conn.commit()
        return

    if not os.path.isfile(LEGACY_JSON_PATH):
        conn.execute(
            "UPDATE metadata SET meta_value = '1' WHERE meta_key = ?",
            ("legacy_json_migrated",),
        )
        conn.commit()
        return

    with open(LEGACY_JSON_PATH, "r", encoding="utf-8") as handle:
        legacy = json.load(handle)

    for file_record in legacy.get("files", []):
        conn.execute(
            """
            INSERT OR REPLACE INTO files(
                file_id, transaction_id, original_filename, stored_filename, file_path,
                size_bytes, sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_record.get("file_id"),
                file_record.get("transaction_id"),
                file_record.get("original_filename", ""),
                file_record.get("stored_filename", ""),
                file_record.get("file_path", ""),
                file_record.get("size_bytes", 0),
                file_record.get("sha256", ""),
                file_record.get("created_at", ""),
            ),
        )
        _set_counter_floor(conn, "file", file_record.get("file_id"))

    for input_record in legacy.get("inputs", []):
        conn.execute(
            """
            INSERT OR REPLACE INTO inputs(
                input_record_id, transaction_id, file_id, source, payload_json,
                counts_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                input_record.get("input_record_id"),
                input_record.get("transaction_id"),
                input_record.get("file_id"),
                input_record.get("source", ""),
                json.dumps(input_record.get("payload")),
                json.dumps(input_record.get("counts")),
                input_record.get("created_at", ""),
            ),
        )
        _set_counter_floor(conn, "input", input_record.get("input_record_id"))

    for output_record in legacy.get("outputs", []):
        conn.execute(
            """
            INSERT OR REPLACE INTO outputs(
                output_record_id, transaction_id, file_id, request_input_record_id,
                results_summary_json, results_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                output_record.get("output_record_id"),
                output_record.get("transaction_id"),
                output_record.get("file_id"),
                output_record.get("request_input_record_id"),
                json.dumps(output_record.get("results_summary")),
                json.dumps(output_record.get("results")),
                output_record.get("created_at", ""),
            ),
        )
        _set_counter_floor(conn, "output", output_record.get("output_record_id"))

    for operation in legacy.get("operations", []):
        conn.execute(
            """
            INSERT OR REPLACE INTO operations(
                operation_id, transaction_id, type, file_id, source_input_record_id,
                request_input_record_id, output_record_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation.get("operation_id"),
                operation.get("transaction_id"),
                operation.get("type", ""),
                operation.get("file_id"),
                operation.get("source_input_record_id") or operation.get("input_record_id"),
                operation.get("request_input_record_id"),
                operation.get("output_record_id"),
                operation.get("created_at", ""),
            ),
        )
        _set_counter_floor(conn, "operation", operation.get("operation_id"))

    for transaction in legacy.get("transactions", []):
        conn.execute(
            """
            INSERT OR REPLACE INTO transactions(
                transaction_id, file_id, upload_operation_id, source_input_record_id,
                latest_request_input_id, latest_output_record_id, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transaction.get("transaction_id"),
                transaction.get("file_id"),
                transaction.get("upload_operation_id"),
                transaction.get("source_input_record_id"),
                transaction.get("latest_request_input_id"),
                transaction.get("latest_output_record_id"),
                transaction.get("status", "uploaded"),
                transaction.get("created_at", ""),
                transaction.get("updated_at", transaction.get("created_at", "")),
            ),
        )
        _set_counter_floor(conn, "transaction", transaction.get("transaction_id"))

    conn.execute(
        "UPDATE metadata SET meta_value = '1' WHERE meta_key = ?",
        ("legacy_json_migrated",),
    )
    conn.commit()


def _initialize() -> None:
    with _LOCK:
        conn = _connect()
        try:
            _create_schema(conn)
            _migrate_legacy_json(conn)
        finally:
            conn.close()


def _next_id(conn: sqlite3.Connection, counter_name: str, prefix: str) -> str:
    row = conn.execute("SELECT value FROM counters WHERE name = ?", (counter_name,)).fetchone()
    value = int(row["value"]) + 1
    conn.execute("UPDATE counters SET value = ? WHERE name = ?", (value, counter_name))
    return f"{prefix}-{value:06d}"


def _json_loads(value: Optional[str]) -> Any:
    if not value:
        return None
    return json.loads(value)


def _summarize_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    feasible = [item for item in results if item.get("feasible")]
    # Prefer minimum shift so preferred check-in is selected whenever feasible.
    recommended = min(feasible, key=lambda item: int(item.get("shift_days", 0))) if feasible else None
    return {
        "feasible_count": len(feasible),
        "total_strategies": len(results),
        "recommended_strategy": recommended.get("strategy") if recommended else None,
        "recommended_note": recommended.get("note") if recommended else None,
        "recommended_shift_days": recommended.get("shift_days", 0) if recommended else None,
        "recommended_period_count": len(recommended.get("periods", [])) if recommended else 0,
    }


def register_upload(
    *,
    original_filename: str,
    stored_filename: str,
    file_path: str,
    parsed_input: Optional[Dict[str, Any]],
    clinics: List[Dict[str, Any]],
    existing_schedule: List[Dict[str, Any]],
) -> Dict[str, Any]:
    _initialize()
    with _LOCK:
        conn = _connect()
        try:
            created_at = _now_iso()
            transaction_id = _next_id(conn, "transaction", "TRN")
            upload_operation_id = _next_id(conn, "operation", "OP")
            file_id = _next_id(conn, "file", "FIL")
            input_record_id = _next_id(conn, "input", "INP")

            conn.execute(
                """
                INSERT INTO files(file_id, transaction_id, original_filename, stored_filename, file_path, size_bytes, sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    transaction_id,
                    original_filename,
                    stored_filename,
                    os.path.abspath(file_path),
                    os.path.getsize(file_path),
                    _sha256(file_path),
                    created_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO inputs(input_record_id, transaction_id, file_id, source, payload_json, counts_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    input_record_id,
                    transaction_id,
                    file_id,
                    "upload_parse",
                    json.dumps(parsed_input),
                    json.dumps({"clinics": len(clinics), "existing_periods": len(existing_schedule)}),
                    created_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO operations(operation_id, transaction_id, type, file_id, source_input_record_id, request_input_record_id, output_record_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    upload_operation_id,
                    transaction_id,
                    "upload",
                    file_id,
                    input_record_id,
                    None,
                    None,
                    created_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO transactions(
                    transaction_id, file_id, upload_operation_id, source_input_record_id,
                    latest_request_input_id, latest_output_record_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_id,
                    file_id,
                    upload_operation_id,
                    input_record_id,
                    None,
                    None,
                    "uploaded",
                    created_at,
                    created_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    return {
        "transaction_id": transaction_id,
        "file_id": file_id,
        "upload_operation_id": upload_operation_id,
        "source_input_record_id": input_record_id,
        "stored_at": created_at,
        "storage": "sqlite",
    }


def register_schedule_run(
    *,
    transaction_id: str,
    request_input: Dict[str, Any],
    results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    _initialize()
    with _LOCK:
        conn = _connect()
        try:
            tx = conn.execute(
                "SELECT * FROM transactions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
            if not tx:
                raise ValueError(f"Transaction not found: {transaction_id}")

            created_at = _now_iso()
            request_input_record_id = _next_id(conn, "input", "INP")
            output_record_id = _next_id(conn, "output", "OUT")
            schedule_operation_id = _next_id(conn, "operation", "OP")
            results_summary = _summarize_results(results)
            file_id = tx["file_id"]

            conn.execute(
                """
                INSERT INTO inputs(input_record_id, transaction_id, file_id, source, payload_json, counts_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_input_record_id,
                    transaction_id,
                    file_id,
                    "schedule_request",
                    json.dumps(request_input),
                    None,
                    created_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO outputs(output_record_id, transaction_id, file_id, request_input_record_id, results_summary_json, results_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    output_record_id,
                    transaction_id,
                    file_id,
                    request_input_record_id,
                    json.dumps(results_summary),
                    json.dumps(results),
                    created_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO operations(operation_id, transaction_id, type, file_id, source_input_record_id, request_input_record_id, output_record_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    schedule_operation_id,
                    transaction_id,
                    "schedule_confirm",
                    file_id,
                    tx["source_input_record_id"],
                    request_input_record_id,
                    output_record_id,
                    created_at,
                ),
            )
            conn.execute(
                """
                UPDATE transactions
                SET latest_request_input_id = ?, latest_output_record_id = ?, status = ?, updated_at = ?
                WHERE transaction_id = ?
                """,
                (
                    request_input_record_id,
                    output_record_id,
                    "scheduled",
                    created_at,
                    transaction_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    return {
        "transaction_id": transaction_id,
        "file_id": file_id,
        "source_input_record_id": tx["source_input_record_id"],
        "request_input_record_id": request_input_record_id,
        "output_record_id": output_record_id,
        "schedule_operation_id": schedule_operation_id,
        "stored_at": created_at,
        "results_summary": results_summary,
        "storage": "sqlite",
    }


def get_recent_transactions(limit: int = 10, query: str = "", status: str = "") -> List[Dict[str, Any]]:
    _initialize()
    conn = _connect()
    try:
        clauses = []
        params: List[Any] = []
        query = (query or "").strip()
        status = (status or "").strip()

        if query:
            clauses.append("(t.transaction_id LIKE ? OR t.file_id LIKE ? OR f.original_filename LIKE ? OR IFNULL(o.output_record_id, '') LIKE ?)")
            needle = f"%{query}%"
            params.extend([needle, needle, needle, needle])
        if status:
            clauses.append("t.status = ?")
            params.append(status)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)

        rows = conn.execute(
            f"""
            SELECT
                t.transaction_id,
                t.file_id,
                t.status,
                t.updated_at,
                f.original_filename AS filename,
                t.latest_output_record_id,
                o.results_summary_json
            FROM transactions t
            JOIN files f ON f.file_id = t.file_id
            LEFT JOIN outputs o ON o.output_record_id = t.latest_output_record_id
            {where_sql}
            ORDER BY t.updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

        items = []
        for row in rows:
            summary = _json_loads(row["results_summary_json"]) or {}
            items.append(
                {
                    "transaction_id": row["transaction_id"],
                    "file_id": row["file_id"],
                    "status": row["status"],
                    "filename": row["filename"],
                    "updated_at": row["updated_at"],
                    "latest_output_record_id": row["latest_output_record_id"],
                    "recommended_strategy": summary.get("recommended_strategy"),
                    "feasible_count": summary.get("feasible_count"),
                    "storage": "sqlite",
                }
            )
        return items
    finally:
        conn.close()


def get_transaction_details(transaction_id: str) -> Optional[Dict[str, Any]]:
    _initialize()
    conn = _connect()
    try:
        tx = conn.execute(
            "SELECT * FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
        if not tx:
            return None

        file_row = conn.execute("SELECT * FROM files WHERE file_id = ?", (tx["file_id"],)).fetchone()
        input_rows = conn.execute(
            "SELECT * FROM inputs WHERE transaction_id = ? ORDER BY created_at ASC",
            (transaction_id,),
        ).fetchall()
        output_rows = conn.execute(
            "SELECT * FROM outputs WHERE transaction_id = ? ORDER BY created_at ASC",
            (transaction_id,),
        ).fetchall()
        op_rows = conn.execute(
            "SELECT * FROM operations WHERE transaction_id = ? ORDER BY created_at ASC",
            (transaction_id,),
        ).fetchall()

        return {
            "transaction_id": tx["transaction_id"],
            "file_id": tx["file_id"],
            "status": tx["status"],
            "created_at": tx["created_at"],
            "updated_at": tx["updated_at"],
            "file": dict(file_row) if file_row else None,
            "inputs": [
                {
                    **dict(row),
                    "payload": _json_loads(row["payload_json"]),
                    "counts": _json_loads(row["counts_json"]),
                }
                for row in input_rows
            ],
            "outputs": [
                {
                    **dict(row),
                    "results_summary": _json_loads(row["results_summary_json"]),
                    "results": _json_loads(row["results_json"]),
                }
                for row in output_rows
            ],
            "operations": [dict(row) for row in op_rows],
        }
    finally:
        conn.close()


def delete_transaction(transaction_id: str) -> Dict[str, Any]:
    _initialize()
    with _LOCK:
        conn = _connect()
        try:
            tx = conn.execute(
                "SELECT * FROM transactions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
            if not tx:
                return {"deleted": False, "transaction_id": transaction_id}

            file_id = tx["file_id"]
            file_row = conn.execute(
                "SELECT file_path FROM files WHERE file_id = ?",
                (file_id,),
            ).fetchone()
            file_path = file_row["file_path"] if file_row else None

            # Remove related rows first, then the parent transaction row.
            conn.execute("DELETE FROM operations WHERE transaction_id = ?", (transaction_id,))
            conn.execute("DELETE FROM outputs WHERE transaction_id = ?", (transaction_id,))
            conn.execute("DELETE FROM inputs WHERE transaction_id = ?", (transaction_id,))
            conn.execute("DELETE FROM transactions WHERE transaction_id = ?", (transaction_id,))
            conn.execute("DELETE FROM files WHERE file_id = ?", (file_id,))
            conn.commit()
        finally:
            conn.close()

    file_deleted = False
    if file_path:
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
                file_deleted = True
        except OSError:
            file_deleted = False

    return {
        "deleted": True,
        "transaction_id": transaction_id,
        "file_id": file_id,
        "file_deleted": file_deleted,
    }


def delete_all_transactions() -> Dict[str, Any]:
    _initialize()
    with _LOCK:
        conn = _connect()
        try:
            tx_count_row = conn.execute("SELECT COUNT(*) AS cnt FROM transactions").fetchone()
            tx_count = int(tx_count_row["cnt"]) if tx_count_row else 0

            file_rows = conn.execute("SELECT file_path FROM files").fetchall()
            file_paths = [row["file_path"] for row in file_rows if row and row["file_path"]]

            conn.execute("DELETE FROM operations")
            conn.execute("DELETE FROM outputs")
            conn.execute("DELETE FROM inputs")
            conn.execute("DELETE FROM transactions")
            conn.execute("DELETE FROM files")
            conn.commit()
        finally:
            conn.close()

    deleted_files = 0
    for path in set(file_paths):
        try:
            if os.path.isfile(path):
                os.remove(path)
                deleted_files += 1
        except OSError:
            continue

    return {
        "deleted": True,
        "transactions_deleted": tx_count,
        "files_deleted": deleted_files,
    }


_initialize()
