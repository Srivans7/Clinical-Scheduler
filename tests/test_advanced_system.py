import datetime
import io
import json
from pathlib import Path

import pytest

import app.__init__ as app_module
import app.audit_store as audit_store
from app.excel_parser import _parse_clinic_allocation
from app.models import Clinic, NewStudyInput, StudyPeriod, date_to_serial, serial_to_iso
from app.scheduler import run_all_strategies, compute_utilization, _occupied_capacity


def _map_to_raw(mapping: dict[str, int]) -> str:
    if not mapping:
        return ""
    return " + ".join(f"{cid} ({count})" for cid, count in mapping.items())


def _make_period(protocol: str, day_serial: int, male_map: dict[str, int], female_map: dict[str, int], los: int = 1) -> StudyPeriod:
    checkout = day_serial + los - 1
    return StudyPeriod(
        protocol=protocol,
        period_label="I",
        male_count=sum(male_map.values()),
        female_count=sum(female_map.values()),
        male_clinic=_map_to_raw(male_map),
        female_clinic=_map_to_raw(female_map),
        checkin_serial=day_serial,
        checkout_serial=checkout,
        planned_wo=None,
        actual_wo=None,
        los=los,
        male_clinic_map=male_map,
        female_clinic_map=female_map,
    )


def _assert_constraints(feasible_result: dict, study: NewStudyInput) -> None:
    # Skip constraint checks for optional strategies (they have different result structure)
    if feasible_result.get("optional"):
        return
    
    assert feasible_result["feasible"] is True
    assert feasible_result["shift_days"] >= 0
    periods = feasible_result["periods"]
    assert len(periods) == study.periods

    for period in periods:
        male_map = period["male_clinics"]
        female_map = period["female_clinics"]

        assert set(male_map.keys()).isdisjoint(set(female_map.keys()))
        assert len(male_map) <= 2
        assert len(female_map) <= 2
        assert sum(male_map.values()) == study.male_count
        assert sum(female_map.values()) == study.female_count


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    uploads_dir = tmp_path / "uploads"
    data_dir = tmp_path / "data"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(app_module, "UPLOAD_FOLDER", str(uploads_dir))
    app_module.app.config["UPLOAD_FOLDER"] = str(uploads_dir)
    app_module.app.config["TESTING"] = True
    app_module.app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

    monkeypatch.setattr(audit_store, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(audit_store, "DB_PATH", str(data_dir / "audit_store.db"))
    monkeypatch.setattr(audit_store, "LEGACY_JSON_PATH", str(data_dir / "audit_store.json"))
    audit_store._initialize()

    return {
        "uploads_dir": uploads_dir,
        "data_dir": data_dir,
        "db_path": data_dir / "audit_store.db",
    }


PARSE_CASES = [
    ("1 (48)", {"1": 48}),
    ("1 (50) + 2 (14)", {"1": 50, "2": 14}),
    ("5A (20) + 5B (12)", {"5A": 20, "5B": 12}),
    ("3A (9)", {"3A": 9}),
    (" 3A (9) ", {"3A": 9}),
    ("3A (9)+3B (8)", {"3A": 9, "3B": 8}),
    ("3A(9) + 3B (8)", {"3A": 9, "3B": 8}),
    ("4 (0)", {"4": 0}),
    ("7 (100)", {"7": 100}),
    ("1 (12) + 2 (12) + 3 (12)", {"1": 12, "2": 12, "3": 12}),
    ("", {}),
    ("   ", {}),
    ("invalid", {}),
    ("1 48", {}),
    ("1()", {}),
    ("(12)", {}),
    ("A (x)", {}),
    ("1 (12) + invalid", {"1": 12}),
    ("1 (12)+invalid+3 (6)", {"1": 12, "3": 6}),
    ("A1 (11)", {"A1": 11}),
    ("A_1 (11)", {"A_1": 11}),
    ("01 (11)", {"01": 11}),
    ("X (1) + Y (2)", {"X": 1, "Y": 2}),
    ("X (1) + Y (2) + Z (3)", {"X": 1, "Y": 2, "Z": 3}),
    ("clinic (9)", {"clinic": 9}),
]


@pytest.mark.parametrize("raw, expected", PARSE_CASES)
def test_parse_clinic_allocation_cases(raw: str, expected: dict[str, int]):
    assert _parse_clinic_allocation(raw) == expected


VALIDATION_CASES = [
    ({"protocol": "B", "preferred_checkin": "2026-03-25", "male": 10, "female": 10, "periods": 2, "washout": 7, "los": 3}, []),
    ({"protocol": "", "preferred_checkin": "2026-03-25", "male": 10, "female": 10, "periods": 2, "washout": 7, "los": 3}, ["Protocol is required."]),
    ({"protocol": "B", "preferred_checkin": "", "male": 10, "female": 10, "periods": 2, "washout": 7, "los": 3}, ["Preferred check-in date is required."]),
    ({"protocol": "B", "preferred_checkin": "bad-date", "male": 10, "female": 10, "periods": 2, "washout": 7, "los": 3}, ["Preferred check-in must be a valid ISO date."]),
    ({"protocol": "B", "preferred_checkin": "2026-03-25", "male": -1, "female": 10, "periods": 2, "washout": 7, "los": 3}, ["Male participants must be at least 0."]),
    ({"protocol": "B", "preferred_checkin": "2026-03-25", "male": 10, "female": -1, "periods": 2, "washout": 7, "los": 3}, ["Female participants must be at least 0."]),
    ({"protocol": "B", "preferred_checkin": "2026-03-25", "male": 10, "female": 10, "periods": 0, "washout": 7, "los": 3}, ["Number of periods must be at least 1."]),
    ({"protocol": "B", "preferred_checkin": "2026-03-25", "male": 10, "female": 10, "periods": 2, "washout": -1, "los": 3}, ["Washout days must be at least 0."]),
    ({"protocol": "B", "preferred_checkin": "2026-03-25", "male": 10, "female": 10, "periods": 2, "washout": 7, "los": 0}, ["Length of stay must be at least 1."]),
    ({"protocol": "B", "preferred_checkin": "2026-03-25", "male": 0, "female": 0, "periods": 2, "washout": 7, "los": 3}, ["At least one participant is required."]),
    ({"protocol": "B", "preferred_checkin": "2026-03-25", "male": "x", "female": 10, "periods": 2, "washout": 7, "los": 3}, ["Male participants must be a whole number."]),
    ({"protocol": "B", "preferred_checkin": "2026-03-25", "male": 10, "female": "x", "periods": 2, "washout": 7, "los": 3}, ["Female participants must be a whole number."]),
    ({"protocol": "B", "preferred_checkin": "2026-03-25", "male": 10, "female": 10, "periods": "x", "washout": 7, "los": 3}, ["Number of periods must be a whole number."]),
    ({"protocol": "B", "preferred_checkin": "2026-03-25", "male": 10, "female": 10, "periods": 2, "washout": "x", "los": 3}, ["Washout days must be a whole number."]),
    ({"protocol": "B", "preferred_checkin": "2026-03-25", "male": 10, "female": 10, "periods": 2, "washout": 7, "los": "x"}, ["Length of stay must be a whole number."]),
    ({"protocol": " A ", "preferred_checkin": "2026-12-31", "male": 1, "female": 0, "periods": 1, "washout": 0, "los": 1}, []),
    ({"protocol": "AA", "preferred_checkin": "2026-01-01", "male": 60, "female": 40, "periods": 3, "washout": 14, "los": 4}, []),
    ({"protocol": "AA", "preferred_checkin": "2026-02-28", "male": 2, "female": 3, "periods": 1, "washout": 0, "los": 1}, []),
    ({"protocol": "AA", "preferred_checkin": "2026-02-29", "male": 2, "female": 3, "periods": 1, "washout": 0, "los": 1}, ["Preferred check-in must be a valid ISO date."]),
    ({"protocol": "AA", "preferred_checkin": "2026-11-30", "male": 0, "female": 5, "periods": 1, "washout": 0, "los": 1}, []),
]


@pytest.mark.parametrize("payload, expected_messages", VALIDATION_CASES)
def test_validate_payload_cases(payload: dict, expected_messages: list[str]):
    _normalized, errors = app_module._validate_new_study_payload(payload)
    for msg in expected_messages:
        assert msg in errors
    if not expected_messages:
        assert errors == []


def test_summary_recommends_minimum_shift_feasible_strategy():
    results = [
        {"strategy": "Shift", "feasible": True, "shift_days": 15, "periods": [{"period_num": 1}], "note": "Shifted 15 day(s)"},
        {"strategy": "Split", "feasible": True, "shift_days": 0, "periods": [{"period_num": 1}], "note": "Split only"},
        {"strategy": "Shift+Split", "feasible": True, "shift_days": 5, "periods": [{"period_num": 1}], "note": "Shifted 5 day(s)"},
    ]

    summary = app_module._build_results_summary(results)

    assert summary["recommended_strategy"] == "Split"
    assert summary["recommended_shift_days"] == 0


def test_audit_summary_recommends_minimum_shift_feasible_strategy():
    results = [
        {"strategy": "Shift", "feasible": True, "shift_days": 15, "periods": [{"period_num": 1}], "note": "Shifted 15 day(s)"},
        {"strategy": "Split", "feasible": True, "shift_days": 0, "periods": [{"period_num": 1}], "note": "Split only"},
        {"strategy": "Shift+Split", "feasible": True, "shift_days": 5, "periods": [{"period_num": 1}], "note": "Shifted 5 day(s)"},
    ]

    summary = audit_store._summarize_results(results)

    assert summary["recommended_strategy"] == "Split"
    assert summary["recommended_shift_days"] == 0


SCHED_MATRIX = [5, 20, 40, 55, 60, 65]


@pytest.mark.parametrize("male_count", SCHED_MATRIX)
@pytest.mark.parametrize("female_count", SCHED_MATRIX)
def test_scheduler_matrix_feasibility_and_constraints(male_count: int, female_count: int):
    clinics = [
        Clinic("1", 30),
        Clinic("2", 30),
        Clinic("3", 30),
        Clinic("4", 30),
    ]
    preferred = date_to_serial(datetime.date(2026, 3, 25))
    study = NewStudyInput(
        protocol="N1",
        male_count=male_count,
        female_count=female_count,
        periods=2,
        washout_days=7,
        los=3,
        preferred_checkin_serial=preferred,
    )

    results = run_all_strategies(study, clinics, periods=[])
    any_feasible = any(item["feasible"] for item in results)

    expected_feasible = male_count <= 60 and female_count <= 60 and (male_count + female_count) <= 120
    assert any_feasible == expected_feasible

    for result in results:
        if result["feasible"]:
            _assert_constraints(result, study)


@pytest.mark.parametrize("shift_days", list(range(10)))
def test_shift_strategy_finds_first_open_day(shift_days: int):
    base = date_to_serial(datetime.date(2026, 3, 25))
    clinics = [Clinic("1", 30), Clinic("2", 30), Clinic("3", 30), Clinic("4", 30)]

    existing = []
    if shift_days > 0:
        for clinic_id in ["1", "2", "3", "4"]:
            existing.append(
                _make_period(
                    protocol=f"BLK-{clinic_id}",
                    day_serial=base,
                    male_map={clinic_id: 30},
                    female_map={},
                    los=shift_days,
                )
            )

    study = NewStudyInput(
        protocol="NEW",
        male_count=10,
        female_count=10,
        periods=1,
        washout_days=0,
        los=1,
        preferred_checkin_serial=base,
    )

    shift_result = run_all_strategies(study, clinics, existing)[0]
    assert shift_result["strategy"] == "Shift"
    if shift_days == 0:
        assert shift_result["feasible"] is True
        assert shift_result["shift_days"] == 0
        _assert_constraints(shift_result, study)
    else:
        assert shift_result["feasible"] is False


@pytest.mark.parametrize("los", list(range(1, 11)))
def test_c2_blocks_third_study_at_preferred_date_but_allows_shift(los: int):
    base = date_to_serial(datetime.date(2026, 3, 25))
    clinics = [Clinic("1", 100)]
    existing = [
        _make_period("A", base, {"1": 10}, {}, los=los),
        _make_period("B", base, {"1": 10}, {}, los=los),
    ]

    study = NewStudyInput(
        protocol="C",
        male_count=5,
        female_count=0,
        periods=1,
        washout_days=0,
        los=1,
        preferred_checkin_serial=base,
    )

    shift_result, split_result, shift_split_result, alt_result = run_all_strategies(study, clinics, existing)

    # Split keeps preferred date, so C2 should block the third distinct study.
    assert split_result["strategy"] == "Split"
    assert split_result["feasible"] is False

    # Shift stays fixed at preferred date and should fail; Shift+Split can move.
    assert shift_result["strategy"] == "Shift"
    assert shift_result["feasible"] is False

    assert shift_split_result["strategy"] == "Shift+Split"
    assert shift_split_result["feasible"] is True
    assert shift_split_result["shift_days"] == los


@pytest.fixture()
def fake_parser(monkeypatch: pytest.MonkeyPatch):
    base = date_to_serial(datetime.date(2026, 3, 25))
    clinics = [Clinic("1", 50), Clinic("2", 50), Clinic("3", 50), Clinic("4", 50)]
    existing = [
        _make_period("EX1", base, {"1": 20}, {"2": 20}, los=2),
        _make_period("EX2", base + 10, {"3": 15}, {"4": 15}, los=2),
    ]
    new_study = NewStudyInput(
        protocol="B",
        male_count=20,
        female_count=20,
        periods=2,
        washout_days=7,
        los=3,
        preferred_checkin_serial=base,
    )

    def _fake_parse_excel(_path):
        return clinics, existing, new_study

    monkeypatch.setattr(app_module, "parse_excel", _fake_parse_excel)


@pytest.mark.parametrize(
    "endpoint, payload, expected_status",
    [
        ("/api/schedule-preview", {}, 400),
        ("/api/schedule-preview", {"filename": "x.xlsx", "new_study": {}}, 400),
        ("/api/schedule-preview", {"filename": "missing.xlsx", "transaction_id": "TRN-1", "new_study": {}}, 404),
        ("/api/schedule-confirm", {}, 400),
        ("/api/schedule-confirm", {"filename": "x.xlsx", "new_study": {}}, 400),
        ("/api/schedule-confirm", {"filename": "missing.xlsx", "transaction_id": "TRN-1", "new_study": {}}, 404),
    ],
)
def test_api_schedule_endpoints_bad_payloads(isolated_env, fake_parser, endpoint: str, payload: dict, expected_status: int):
    client = app_module.app.test_client()
    response = client.post(endpoint, json=payload)
    assert response.status_code == expected_status


@pytest.mark.parametrize(
    "new_study_patch",
    [
        {"protocol": "", "male": 10, "female": 10, "periods": 2, "washout": 7, "los": 3},
        {"protocol": "B", "preferred_checkin": "", "male": 10, "female": 10, "periods": 2, "washout": 7, "los": 3},
        {"protocol": "B", "preferred_checkin": "2026-03-25", "male": -1, "female": 10, "periods": 2, "washout": 7, "los": 3},
        {"protocol": "B", "preferred_checkin": "2026-03-25", "male": 10, "female": -1, "periods": 2, "washout": 7, "los": 3},
        {"protocol": "B", "preferred_checkin": "2026-03-25", "male": 0, "female": 0, "periods": 2, "washout": 7, "los": 3},
        {"protocol": "B", "preferred_checkin": "bad-date", "male": 10, "female": 10, "periods": 2, "washout": 7, "los": 3},
    ],
)
def test_api_preview_validation_errors(isolated_env, fake_parser, new_study_patch: dict):
    client = app_module.app.test_client()

    upload = client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"xlsx"), "case.xlsx")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    payload = upload.get_json()

    new_study = payload["new_study"].copy()
    new_study.update(new_study_patch)

    response = client.post(
        "/api/schedule-preview",
        json={
            "filename": payload["filename"],
            "transaction_id": payload["audit"]["transaction_id"],
            "new_study": new_study,
        },
    )
    assert response.status_code == 400
    body = response.get_json()
    assert "errors" in body
    assert body["errors"]


def test_upload_preview_confirm_and_history_linkage(isolated_env, fake_parser):
    client = app_module.app.test_client()

    upload = client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"xlsx"), "case.xlsx")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    upload_json = upload.get_json()
    transaction_id = upload_json["audit"]["transaction_id"]

    details_after_upload = audit_store.get_transaction_details(transaction_id)
    assert details_after_upload is not None
    assert details_after_upload["status"] == "uploaded"
    assert len(details_after_upload["outputs"]) == 0
    assert len(details_after_upload["inputs"]) == 1

    preview = client.post(
        "/api/schedule-preview",
        json={
            "filename": upload_json["filename"],
            "transaction_id": transaction_id,
            "new_study": upload_json["new_study"],
        },
    )
    assert preview.status_code == 200
    preview_json = preview.get_json()
    assert "summary" in preview_json
    assert len(preview_json["results"]) == 4  # 3 main strategies + 1 optional alternative dates

    details_after_preview = audit_store.get_transaction_details(transaction_id)
    assert details_after_preview is not None
    assert len(details_after_preview["outputs"]) == 0

    confirm = client.post(
        "/api/schedule-confirm",
        json={
            "filename": upload_json["filename"],
            "transaction_id": transaction_id,
            "new_study": upload_json["new_study"],
        },
    )
    assert confirm.status_code == 200
    confirm_json = confirm.get_json()

    assert confirm_json["audit"]["transaction_id"] == transaction_id
    assert confirm_json["audit"]["source_input_record_id"] == upload_json["audit"]["source_input_record_id"]
    assert confirm_json["audit"]["request_input_record_id"].startswith("INP-")
    assert confirm_json["audit"]["output_record_id"].startswith("OUT-")
    assert confirm_json["audit"]["schedule_operation_id"].startswith("OP-")

    history = client.get("/api/audit-log?limit=20&q=case.xlsx&status=scheduled")
    assert history.status_code == 200
    history_items = history.get_json()["items"]
    assert any(item["transaction_id"] == transaction_id for item in history_items)

    detail = client.get(f"/api/audit-log/{transaction_id}")
    assert detail.status_code == 200
    detail_json = detail.get_json()
    assert detail_json["status"] == "scheduled"
    assert len(detail_json["inputs"]) == 2
    assert len(detail_json["outputs"]) == 1


def test_api_recommends_preferred_date_when_split_feasible_and_shift_delayed(isolated_env, monkeypatch: pytest.MonkeyPatch):
    base = date_to_serial(datetime.date(2026, 4, 2))
    clinics = [Clinic("A", 10), Clinic("B", 8)]
    existing = [_make_period("EXISTING", base, {"A": 8}, {}, los=15)]
    new_study = NewStudyInput(
        protocol="NEW",
        male_count=10,
        female_count=0,
        periods=1,
        washout_days=0,
        los=1,
        preferred_checkin_serial=base,
    )

    def _fake_parse_excel(_path):
        return clinics, existing, new_study

    monkeypatch.setattr(app_module, "parse_excel", _fake_parse_excel)
    client = app_module.app.test_client()

    upload = client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"xlsx"), "prefer-date.xlsx")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    upload_json = upload.get_json()

    payload = {
        "filename": upload_json["filename"],
        "transaction_id": upload_json["audit"]["transaction_id"],
        "new_study": upload_json["new_study"],
    }

    preview = client.post("/api/schedule-preview", json=payload)
    assert preview.status_code == 200
    preview_json = preview.get_json()
    shift_result, split_result, shift_split_result, alt_result = preview_json["results"]

    assert shift_result["strategy"] == "Shift"
    assert shift_result["feasible"] is False

    assert split_result["strategy"] == "Split"
    assert split_result["feasible"] is True
    assert split_result["shift_days"] == 0

    assert shift_split_result["strategy"] == "Shift+Split"
    assert shift_split_result["feasible"] is True
    assert shift_split_result["shift_days"] == 0

    assert preview_json["summary"]["recommended_strategy"] == "Split"
    assert preview_json["summary"]["recommended_shift_days"] == 0

    confirm = client.post("/api/schedule-confirm", json=payload)
    assert confirm.status_code == 200
    confirm_json = confirm.get_json()
    assert confirm_json["summary"]["recommended_strategy"] == "Split"
    assert confirm_json["summary"]["recommended_shift_days"] == 0

    detail = client.get(f"/api/audit-log/{upload_json['audit']['transaction_id']}")
    assert detail.status_code == 200
    detail_json = detail.get_json()
    summary = detail_json["outputs"][0]["results_summary"]
    assert summary["recommended_strategy"] == "Split"
    assert summary["recommended_shift_days"] == 0


def test_delete_transaction_removes_audit_records(isolated_env, fake_parser):
    client = app_module.app.test_client()

    upload = client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"xlsx"), "case-delete.xlsx")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    upload_json = upload.get_json()
    transaction_id = upload_json["audit"]["transaction_id"]

    saved_path = isolated_env["uploads_dir"] / upload_json["filename"]
    assert saved_path.exists()

    delete_response = client.delete(f"/api/audit-log/{transaction_id}")
    assert delete_response.status_code == 200
    delete_json = delete_response.get_json()
    assert delete_json["deleted"] is True
    assert delete_json["transaction_id"] == transaction_id

    detail_after = client.get(f"/api/audit-log/{transaction_id}")
    assert detail_after.status_code == 404

    history = client.get("/api/audit-log?limit=20&q=case-delete.xlsx")
    assert history.status_code == 200
    history_items = history.get_json()["items"]
    assert not any(item["transaction_id"] == transaction_id for item in history_items)

    assert not saved_path.exists()

    second_delete = client.delete(f"/api/audit-log/{transaction_id}")
    assert second_delete.status_code == 404


def test_deleted_history_does_not_reappear_from_legacy_json(isolated_env):
    data_dir = isolated_env["data_dir"]
    legacy_path = data_dir / "audit_store.json"

    legacy_payload = {
        "files": [
            {
                "file_id": "FIL-000100",
                "transaction_id": "TRN-000100",
                "original_filename": "legacy-case.xlsx",
                "stored_filename": "legacy-case.xlsx",
                "file_path": str(isolated_env["uploads_dir"] / "legacy-case.xlsx"),
                "size_bytes": 10,
                "sha256": "legacy",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ],
        "inputs": [
            {
                "input_record_id": "INP-000100",
                "transaction_id": "TRN-000100",
                "file_id": "FIL-000100",
                "source": "upload_parse",
                "payload": {"protocol": "LEGACY"},
                "counts": {"clinics": 1, "existing_periods": 0},
                "created_at": "2026-01-01T00:00:00Z",
            }
        ],
        "outputs": [],
        "operations": [
            {
                "operation_id": "OP-000100",
                "transaction_id": "TRN-000100",
                "type": "upload",
                "file_id": "FIL-000100",
                "source_input_record_id": "INP-000100",
                "request_input_record_id": None,
                "output_record_id": None,
                "created_at": "2026-01-01T00:00:00Z",
            }
        ],
        "transactions": [
            {
                "transaction_id": "TRN-000100",
                "file_id": "FIL-000100",
                "upload_operation_id": "OP-000100",
                "source_input_record_id": "INP-000100",
                "latest_request_input_id": None,
                "latest_output_record_id": None,
                "status": "uploaded",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ],
    }
    legacy_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    db_path = data_dir / "audit_store.db"
    if db_path.exists():
        db_path.unlink()

    audit_store._initialize()

    initial = audit_store.get_recent_transactions(limit=10)
    assert any(item["transaction_id"] == "TRN-000100" for item in initial)

    delete_result = audit_store.delete_transaction("TRN-000100")
    assert delete_result["deleted"] is True

    after_delete = audit_store.get_recent_transactions(limit=10)
    assert all(item["transaction_id"] != "TRN-000100" for item in after_delete)


def test_history_page_route(isolated_env):
    client = app_module.app.test_client()
    response = client.get("/history")
    assert response.status_code == 200
    assert b"Stored Scheduling History" in response.data


def test_upload_errors_and_content_limits(isolated_env, fake_parser):
    client = app_module.app.test_client()

    no_file = client.post("/api/upload", data={}, content_type="multipart/form-data")
    assert no_file.status_code == 400

    empty_name = client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"x"), "")},
        content_type="multipart/form-data",
    )
    assert empty_name.status_code == 400

    wrong_ext = client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"x"), "case.txt")},
        content_type="multipart/form-data",
    )
    assert wrong_ext.status_code == 400

    app_module.app.config["MAX_CONTENT_LENGTH"] = 2
    too_big = client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"123456"), "case.xlsx")},
        content_type="multipart/form-data",
    )
    assert too_big.status_code == 413


# ──────────────────────────────────────────────────────────────────────────────
# Split strategy working test
# ──────────────────────────────────────────────────────────────────────────────

def test_split_strategy_feasible_when_no_single_clinic_fits():
    """
    Split is the ONLY feasible strategy at the preferred check-in date.

    Setup:
      - Clinic A: 8 beds total, 6 already occupied by existing study EXISTING.
        => 2 free beds at the preferred date.
      - Clinic B: 8 beds total, nothing booked.
        => 8 free beds at the preferred date.
      - New study: 10 males, 0 females.

    Why Shift fails:
      No single clinic ever has >= 10 beds in total (max is 8), so Shift
      (which forbids splitting) is permanently infeasible regardless of date.

    Why Split succeeds:
      2 (Clinic A) + 8 (Clinic B) = 10 >= 10, so all 10 males are placed at
      the preferred date with shift_days == 0.
    """
    import datetime  # noqa: F401 — already at module level; kept for compatibility

    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("A", 8), Clinic("B", 8)]

    # Existing study occupies 6 beds (males) in Clinic A for 5 days.
    existing = [_make_period("EXISTING", base, {"A": 6}, {}, los=5)]

    study = NewStudyInput(
        protocol="NEW",
        male_count=10,
        female_count=0,
        periods=1,
        washout_days=0,
        los=5,
        preferred_checkin_serial=base,
    )

    shift_result, split_result, shift_split_result, alt_result = run_all_strategies(study, clinics, existing)

    # 1. Shift: no split allowed; no single clinic has >=10 beds → always infeasible.
    assert shift_result["strategy"] == "Shift"
    assert shift_result["feasible"] is False

    # 2. Split: stays at preferred date, splits A (2) + B (8) = 10 → feasible.
    assert split_result["strategy"] == "Split"
    assert split_result["feasible"] is True
    assert split_result["shift_days"] == 0          # no date shift needed

    periods = split_result["periods"]
    assert len(periods) == 1
    male_alloc = periods[0]["male_clinics"]
    female_alloc = periods[0]["female_clinics"]

    assert sum(male_alloc.values()) == 10           # all 10 males placed
    assert female_alloc == {}                       # no females
    assert len(male_alloc) == 2                     # split across exactly 2 clinics (C3 ok)
    assert "A" in male_alloc and "B" in male_alloc
    assert male_alloc["A"] == 2                     # only 2 free in A
    assert male_alloc["B"] == 8                     # remaining 8 in B

    # Full constraint check (C1 / C2 / C3).
    _assert_constraints(split_result, study)


# ══════════════════════════════════════════════════════════════════════════════
# Tests for _occupied_capacity bug-fix (dict-merge key collision)
# ══════════════════════════════════════════════════════════════════════════════

def test_occupied_capacity_same_clinic_both_genders():
    """When male_map and female_map share a clinic key the counts must be SUMMED."""
    base = date_to_serial(datetime.date(2026, 4, 1))
    period = _make_period("EX", base, {"1": 30}, {"1": 20}, los=1)
    assert _occupied_capacity("1", base, [period]) == 50  # was 20 before fix


def test_occupied_capacity_different_clinics():
    """Standard case: male and female in separate clinics."""
    base = date_to_serial(datetime.date(2026, 4, 1))
    period = _make_period("EX", base, {"1": 30}, {"2": 20}, los=1)
    assert _occupied_capacity("1", base, [period]) == 30
    assert _occupied_capacity("2", base, [period]) == 20


def test_occupied_capacity_clinic_not_in_period_returns_zero():
    base = date_to_serial(datetime.date(2026, 4, 1))
    period = _make_period("EX", base, {"1": 10}, {}, los=1)
    assert _occupied_capacity("99", base, [period]) == 0


def test_occupied_capacity_empty_maps():
    base = date_to_serial(datetime.date(2026, 4, 1))
    period = _make_period("EX", base, {}, {}, los=1)
    assert _occupied_capacity("1", base, [period]) == 0


def test_occupied_capacity_date_outside_period():
    base = date_to_serial(datetime.date(2026, 4, 1))
    period = _make_period("EX", base, {"1": 20}, {}, los=3)  # days 0,1,2
    assert _occupied_capacity("1", base + 3, [period]) == 0  # day after period ends


def test_occupied_capacity_multiple_periods_same_clinic():
    """Several studies in the same clinic accumulate correctly."""
    base = date_to_serial(datetime.date(2026, 4, 1))
    p1 = _make_period("A", base, {"1": 15}, {}, los=1)
    p2 = _make_period("B", base, {"1": 10}, {}, los=1)
    p3 = _make_period("C", base, {}, {"1": 5}, los=1)
    assert _occupied_capacity("1", base, [p1, p2, p3]) == 30


def test_occupied_capacity_only_on_period_dates():
    """Capacity is only added on days within [checkin, checkout]."""
    base = date_to_serial(datetime.date(2026, 4, 1))
    period = _make_period("EX", base + 2, {"1": 20}, {}, los=2)  # days 2,3
    assert _occupied_capacity("1", base + 0, [period]) == 0
    assert _occupied_capacity("1", base + 1, [period]) == 0
    assert _occupied_capacity("1", base + 2, [period]) == 20
    assert _occupied_capacity("1", base + 3, [period]) == 20
    assert _occupied_capacity("1", base + 4, [period]) == 0


# ══════════════════════════════════════════════════════════════════════════════
# Tests for preferred check-in date being honoured (female-first retry)
# ══════════════════════════════════════════════════════════════════════════════

def test_preferred_date_honored_female_first_retry():
    """
    Clinic A capacity 15, Clinic B capacity 8.  New study: 8 males, 15 females.

    Male-first ordering: males go to A (15 >= 8), then females need >=15
    excluding A => only B left (8 < 15) => fails despite preferred date being free.

    Female-first retry: females go to A (15 >= 15), males go to B (8 >= 8)
    => succeeds at shift_days=0.
    """
    base = date_to_serial(datetime.date(2026, 4, 2))
    clinics = [Clinic("A", 15), Clinic("B", 8)]
    study = NewStudyInput(
        protocol="NEW",
        male_count=8,
        female_count=15,
        periods=1,
        washout_days=0,
        los=1,
        preferred_checkin_serial=base,
    )

    results = run_all_strategies(study, clinics, [])
    shift_result = results[0]

    assert shift_result["feasible"] is True
    assert shift_result["shift_days"] == 0  # must honour preferred date

    male_map   = shift_result["periods"][0]["male_clinics"]
    female_map = shift_result["periods"][0]["female_clinics"]
    assert set(male_map.keys()).isdisjoint(set(female_map.keys()))  # C1
    assert sum(male_map.values())   == 8
    assert sum(female_map.values()) == 15
    _assert_constraints(shift_result, study)


def test_preferred_date_honored_shift_days_zero_when_capacity_available():
    """No existing periods => all strategies schedule at shift_days=0."""
    base = date_to_serial(datetime.date(2026, 4, 2))
    clinics = [Clinic("1", 30), Clinic("2", 30)]
    study = NewStudyInput(
        protocol="NEW",
        male_count=20,
        female_count=20,
        periods=1,
        washout_days=7,
        los=3,
        preferred_checkin_serial=base,
    )
    for result in run_all_strategies(study, clinics, []):
        if result.get("optional"):  # Skip optional strategies
            continue
        assert result["feasible"] is True
        assert result["shift_days"] == 0


def test_preferred_date_honored_all_three_strategies():
    """With plenty of capacity every strategy should use shift_days=0."""
    base = date_to_serial(datetime.date(2026, 4, 2))
    clinics = [Clinic("A", 60), Clinic("B", 60), Clinic("C", 60), Clinic("D", 60)]
    study = NewStudyInput(
        protocol="P",
        male_count=50,
        female_count=50,
        periods=2,
        washout_days=14,
        los=5,
        preferred_checkin_serial=base,
    )
    for result in run_all_strategies(study, clinics, []):
        if result.get("optional"):  # Skip optional strategies
            continue
        assert result["feasible"] is True
        assert result["shift_days"] == 0
        _assert_constraints(result, study)


def test_female_only_study_no_retry_triggered():
    """Female-only study: no retry needed; C1 constraint trivially satisfied."""
    base = date_to_serial(datetime.date(2026, 4, 2))
    clinics = [Clinic("1", 30)]
    study = NewStudyInput(
        protocol="F",
        male_count=0,
        female_count=25,
        periods=1,
        washout_days=0,
        los=2,
        preferred_checkin_serial=base,
    )
    results = run_all_strategies(study, clinics, [])
    shift_result = results[0]
    assert shift_result["feasible"] is True
    assert shift_result["shift_days"] == 0
    assert shift_result["periods"][0]["male_clinics"] == {}


def test_male_only_study_no_retry_triggered():
    """Male-only study: female retry path is never entered."""
    base = date_to_serial(datetime.date(2026, 4, 2))
    clinics = [Clinic("1", 30)]
    study = NewStudyInput(
        protocol="M",
        male_count=25,
        female_count=0,
        periods=1,
        washout_days=0,
        los=2,
        preferred_checkin_serial=base,
    )
    results = run_all_strategies(study, clinics, [])
    shift_result = results[0]
    assert shift_result["feasible"] is True
    assert shift_result["shift_days"] == 0
    assert shift_result["periods"][0]["female_clinics"] == {}


def test_c1_still_enforced_after_female_first_retry():
    """Even after the female-first retry, C1 (no shared clinic) must hold."""
    base = date_to_serial(datetime.date(2026, 4, 2))
    clinics = [Clinic("A", 15), Clinic("B", 8)]
    study = NewStudyInput(
        protocol="NEW",
        male_count=8,
        female_count=15,
        periods=1,
        washout_days=0,
        los=1,
        preferred_checkin_serial=base,
    )
    results = run_all_strategies(study, clinics, [])
    for result in results:
        if result["feasible"] and not result.get("optional"):
            for period in result["periods"]:
                assert set(period["male_clinics"].keys()).isdisjoint(
                    set(period["female_clinics"].keys())
                ), f"C1 violated in {result['strategy']}"


def test_preferred_date_blocked_c2_then_shifts():
    """All clinics already host 2 studies at preferred date => need to shift."""
    base = date_to_serial(datetime.date(2026, 4, 2))
    clinics = [Clinic("1", 100)]
    existing = [
        _make_period("A", base, {"1": 10}, {}, los=3),
        _make_period("B", base, {"1": 10}, {}, los=3),
    ]
    study = NewStudyInput(
        protocol="C",
        male_count=5,
        female_count=0,
        periods=1,
        washout_days=0,
        los=1,
        preferred_checkin_serial=base,
    )
    shift_result, split_result, _, _ = run_all_strategies(study, clinics, existing)
    assert split_result["feasible"] is False
    assert shift_result["feasible"] is False


def test_multi_period_washout_respected():
    """Period-2 check-in must be checkout_1 + washout + 1."""
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 30), Clinic("2", 30)]
    study = NewStudyInput(
        protocol="P",
        male_count=10,
        female_count=10,
        periods=2,
        washout_days=7,
        los=3,
        preferred_checkin_serial=base,
    )
    results = run_all_strategies(study, clinics, [])
    shift_result = results[0]
    assert shift_result["feasible"] is True
    periods = shift_result["periods"]
    p2_expected = periods[0]["checkout_serial"] + 7
    assert periods[1]["checkin_serial"] == p2_expected


def test_multi_period_three_periods_washout():
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 30), Clinic("2", 30)]
    study = NewStudyInput(
        protocol="P", male_count=10, female_count=10,
        periods=3, washout_days=14, los=4,
        preferred_checkin_serial=base,
    )
    results = run_all_strategies(study, clinics, [])
    assert results[0]["feasible"] is True
    periods = results[0]["periods"]
    assert len(periods) == 3
    for i in range(1, 3):
        expected = periods[i - 1]["checkout_serial"] + 14
        assert periods[i]["checkin_serial"] == expected


def test_capacity_fully_occupied_infeasible():
    """All beds full in every clinic => all strategies infeasible."""
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 20), Clinic("2", 20)]
    existing = [
        _make_period("A", base, {"1": 20}, {}, los=200),
        _make_period("B", base, {"2": 20}, {}, los=200),
    ]
    study = NewStudyInput(
        protocol="NEW", male_count=10, female_count=0,
        periods=1, washout_days=0, los=1,
        preferred_checkin_serial=base,
    )
    assert not any(r["feasible"] for r in run_all_strategies(study, clinics, existing))


# ══════════════════════════════════════════════════════════════════════════════
# Tests for compute_utilization
# ══════════════════════════════════════════════════════════════════════════════

def test_compute_utilization_empty_periods():
    clinics = [Clinic("1", 60), Clinic("2", 60)]
    result = compute_utilization(clinics, [])
    assert result["dates"] == []
    assert result["grid"] == {}
    assert result["totals"] == {}
    assert result["clinic_totals"] == {"1": 0, "2": 0}
    assert len(result["clinics"]) == 2


def test_compute_utilization_single_period():
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 60), Clinic("2", 60)]
    existing = [_make_period("A", base, {"1": 30}, {"2": 20}, los=2)]
    result = compute_utilization(clinics, existing)
    d0, d1 = serial_to_iso(base), serial_to_iso(base + 1)
    assert d0 in result["dates"] and d1 in result["dates"]
    assert result["grid"][d0]["1"] == 30
    assert result["grid"][d0]["2"] == 20
    assert result["totals"][d0] == 50
    assert result["clinic_totals"]["1"] == 60   # 30 x 2 days
    assert result["clinic_totals"]["2"] == 40   # 20 x 2 days


def test_compute_utilization_same_clinic_both_genders_bug_fixed():
    """Confirms _occupied_capacity bug fix propagates through compute_utilization."""
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 60)]
    existing = [_make_period("A", base, {"1": 30}, {"1": 20}, los=1)]
    result = compute_utilization(clinics, existing)
    d0 = serial_to_iso(base)
    assert result["grid"][d0]["1"] == 50   # not 20 (old bug)
    assert result["totals"][d0] == 50


def test_compute_utilization_zero_occupancy_dates_excluded():
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 60)]
    existing = [_make_period("A", base + 2, {"1": 10}, {}, los=2)]  # days 2,3
    result = compute_utilization(clinics, existing)
    assert serial_to_iso(base)     not in result["dates"]
    assert serial_to_iso(base + 1) not in result["dates"]
    assert serial_to_iso(base + 2) in result["dates"]
    assert serial_to_iso(base + 3) in result["dates"]
    assert serial_to_iso(base + 4) not in result["dates"]


def test_compute_utilization_multiple_studies_same_day():
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 60), Clinic("2", 60)]
    existing = [
        _make_period("A", base, {"1": 20}, {}, los=1),
        _make_period("B", base, {"1": 15}, {"2": 25}, los=1),
    ]
    result = compute_utilization(clinics, existing)
    d0 = serial_to_iso(base)
    assert result["grid"][d0]["1"] == 35
    assert result["grid"][d0]["2"] == 25
    assert result["totals"][d0] == 60


def test_compute_utilization_clinic_totals():
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 60), Clinic("2", 60)]
    existing = [
        _make_period("A", base, {"1": 10}, {}, los=3),  # clinic 1: 30
        _make_period("B", base, {}, {"2": 20}, los=2),  # clinic 2: 40
    ]
    result = compute_utilization(clinics, existing)
    assert result["clinic_totals"]["1"] == 30
    assert result["clinic_totals"]["2"] == 40


def test_compute_utilization_new_study_overlay():
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 60), Clinic("2", 60)]
    existing = [_make_period("A", base, {"1": 20}, {}, los=1)]
    new_periods = [{
        "checkin_serial": base, "checkout_serial": base,
        "male_clinics": {"2": 15}, "female_clinics": {},
    }]
    result = compute_utilization(clinics, existing, new_periods)
    d0 = serial_to_iso(base)
    assert result["grid"][d0]["1"] == 20
    assert result["grid"][d0]["2"] == 15
    assert result["totals"][d0] == 35


def test_compute_utilization_date_range_covers_all_periods():
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 60)]
    existing = [
        _make_period("A", base,      {"1": 10}, {}, los=2),   # days 0-1
        _make_period("B", base + 10, {"1": 10}, {}, los=3),   # days 10-12
    ]
    result = compute_utilization(clinics, existing)
    for offset in (0, 1, 10, 11, 12):
        assert serial_to_iso(base + offset) in result["dates"]
    for offset in range(2, 10):
        assert serial_to_iso(base + offset) not in result["dates"]


def test_compute_utilization_returns_sorted_dates():
    base = date_to_serial(datetime.date(2026, 3, 28))
    clinics = [Clinic("1", 60)]
    existing = [
        _make_period("B", base + 5, {"1": 5}, {}, los=1),
        _make_period("A", base,     {"1": 5}, {}, los=1),
    ]
    result = compute_utilization(clinics, existing)
    assert result["dates"] == sorted(result["dates"])


# ══════════════════════════════════════════════════════════════════════════════
# Tests for /utilization page route and /api/utilization endpoint
# ══════════════════════════════════════════════════════════════════════════════

def test_utilization_page_route(isolated_env):
    client = app_module.app.test_client()
    response = client.get("/utilization")
    assert response.status_code == 200
    assert b"Bed Utilization" in response.data


def test_utilization_api_missing_transaction_id(isolated_env):
    client = app_module.app.test_client()
    response = client.get("/api/utilization")
    assert response.status_code == 400
    assert "transaction_id" in response.get_json()["error"]


def test_utilization_api_unknown_transaction(isolated_env):
    client = app_module.app.test_client()
    response = client.get("/api/utilization?transaction_id=TRN-999999")
    assert response.status_code == 404


def test_utilization_api_valid_uploaded_transaction(isolated_env, fake_parser):
    client = app_module.app.test_client()
    upload = client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"xlsx"), "case.xlsx")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    txn_id = upload.get_json()["audit"]["transaction_id"]

    response = client.get(f"/api/utilization?transaction_id={txn_id}")
    assert response.status_code == 200
    data = response.get_json()

    assert data["transaction_id"] == txn_id
    assert "dates" in data and "clinics" in data
    assert "grid" in data and "totals" in data and "clinic_totals" in data
    assert isinstance(data["dates"], list)
    assert isinstance(data["clinics"], list)


def test_utilization_api_after_confirm_includes_new_study_periods(isolated_env, fake_parser):
    client = app_module.app.test_client()
    upload = client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"xlsx"), "case.xlsx")},
        content_type="multipart/form-data",
    )
    upload_json = upload.get_json()
    txn_id = upload_json["audit"]["transaction_id"]

    confirm = client.post(
        "/api/schedule-confirm",
        json={
            "filename": upload_json["filename"],
            "transaction_id": txn_id,
            "new_study": upload_json["new_study"],
        },
    )
    assert confirm.status_code == 200

    response = client.get(f"/api/utilization?transaction_id={txn_id}")
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["dates"]) > 0


def test_utilization_api_file_deleted_returns_404(isolated_env, fake_parser):
    import os as _os
    client = app_module.app.test_client()
    upload = client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"xlsx"), "case.xlsx")},
        content_type="multipart/form-data",
    )
    upload_json = upload.get_json()
    txn_id = upload_json["audit"]["transaction_id"]

    file_path = isolated_env["uploads_dir"] / upload_json["filename"]
    if file_path.exists():
        _os.remove(str(file_path))

    response = client.get(f"/api/utilization?transaction_id={txn_id}")
    assert response.status_code == 404
    assert "no longer available" in response.get_json()["error"]


def test_utilization_api_response_structure_complete(isolated_env, fake_parser):
    """All expected keys are present with correct types."""
    client = app_module.app.test_client()
    upload = client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"xlsx"), "case.xlsx")},
        content_type="multipart/form-data",
    )
    txn_id = upload.get_json()["audit"]["transaction_id"]

    data = client.get(f"/api/utilization?transaction_id={txn_id}").get_json()

    assert isinstance(data["dates"],          list)
    assert isinstance(data["clinics"],         list)
    assert isinstance(data["grid"],            dict)
    assert isinstance(data["totals"],          dict)
    assert isinstance(data["clinic_totals"],   dict)
    assert isinstance(data["filename"],        str)
    assert isinstance(data["transaction_id"],  str)

    for clinic in data["clinics"]:
        assert "id" in clinic and "capacity" in clinic

    for date in data["dates"]:
        assert date in data["grid"]
        assert date in data["totals"]


# ══════════════════════════════════════════════════════════════════════════════
# Additional scheduler edge-case tests
# ══════════════════════════════════════════════════════════════════════════════

def test_shift_strategy_exact_capacity_boundary():
    """Shift succeeds when free capacity exactly equals participant count."""
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 30), Clinic("2", 20)]
    existing = [_make_period("EX", base, {"1": 20}, {"2": 10}, los=1)]
    study = NewStudyInput(
        protocol="N", male_count=10, female_count=10,
        periods=1, washout_days=0, los=1,
        preferred_checkin_serial=base,
    )
    results = run_all_strategies(study, clinics, existing)
    assert any(r["feasible"] for r in results)
    for r in results:
        if r["feasible"]:
            _assert_constraints(r, study)


def test_shift_one_participant_always_feasible():
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 1)]
    study = NewStudyInput(
        protocol="TINY", male_count=1, female_count=0,
        periods=1, washout_days=0, los=1,
        preferred_checkin_serial=base,
    )
    results = run_all_strategies(study, clinics, [])
    assert results[0]["feasible"] is True
    assert results[0]["shift_days"] == 0


def test_c3_max_two_clinics_per_gender():
    """C3: split allocation spans at most 2 clinics per gender."""
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 10), Clinic("2", 10), Clinic("3", 10), Clinic("4", 10)]
    study = NewStudyInput(
        protocol="P", male_count=30, female_count=0,
        periods=1, washout_days=0, los=1,
        preferred_checkin_serial=base,
    )
    results = run_all_strategies(study, clinics, [])
    for r in results:
        if r["feasible"]:
            for period in r["periods"]:
                assert len(period["male_clinics"]) <= 2
                assert len(period["female_clinics"]) <= 2


def test_single_clinic_gender_mix_infeasible():
    """A single clinic cannot hold both males and females (C1)."""
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 100)]
    study = NewStudyInput(
        protocol="P", male_count=10, female_count=10,
        periods=1, washout_days=0, los=1,
        preferred_checkin_serial=base,
    )
    results = run_all_strategies(study, clinics, [])
    assert all(not r["feasible"] for r in results)


def test_two_clinics_gender_separation():
    """Two clinics, one per gender => feasible at preferred date."""
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("M", 20), Clinic("F", 20)]
    study = NewStudyInput(
        protocol="P", male_count=20, female_count=20,
        periods=1, washout_days=0, los=1,
        preferred_checkin_serial=base,
    )
    results = run_all_strategies(study, clinics, [])
    assert results[0]["feasible"] is True
    assert results[0]["shift_days"] == 0
    _assert_constraints(results[0], study)


@pytest.mark.parametrize("n_periods", [1, 2, 3, 4])
def test_period_count_respected(n_periods: int):
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 30), Clinic("2", 30)]
    study = NewStudyInput(
        protocol="P", male_count=10, female_count=10,
        periods=n_periods, washout_days=7, los=3,
        preferred_checkin_serial=base,
    )
    results = run_all_strategies(study, clinics, [])
    for r in results:
        if r["feasible"] and not r.get("optional"):
            assert len(r["periods"]) == n_periods


@pytest.mark.parametrize("los", [1, 3, 7, 14, 28])
def test_los_reflected_in_checkout_date(los: int):
    """checkout_serial must equal checkin_serial + los - 1."""
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 30), Clinic("2", 30)]
    study = NewStudyInput(
        protocol="P", male_count=10, female_count=10,
        periods=1, washout_days=0, los=los,
        preferred_checkin_serial=base,
    )
    results = run_all_strategies(study, clinics, [])
    for r in results:
        if r["feasible"] and not r.get("optional"):
            period = r["periods"][0]
            assert period["checkout_serial"] == period["checkin_serial"] + los - 1


def test_shift_strategy_never_splits_participants():
    """Shift strategy: each gender uses exactly 1 clinic."""
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 30), Clinic("2", 30), Clinic("3", 30)]
    study = NewStudyInput(
        protocol="P", male_count=20, female_count=20,
        periods=2, washout_days=7, los=3,
        preferred_checkin_serial=base,
    )
    shift_result = run_all_strategies(study, clinics, [])[0]
    assert shift_result["feasible"] is True
    for period in shift_result["periods"]:
        assert len(period["male_clinics"])   == 1
        assert len(period["female_clinics"]) == 1


def test_split_strategy_shift_days_always_zero():
    """Split strategy must always return shift_days = 0."""
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 30), Clinic("2", 30)]
    study = NewStudyInput(
        protocol="P", male_count=20, female_count=20,
        periods=1, washout_days=0, los=1,
        preferred_checkin_serial=base,
    )
    split_result = run_all_strategies(study, clinics, [])[1]
    if split_result["feasible"]:
        assert split_result["shift_days"] == 0


def test_shift_split_shift_days_nonnegative():
    base = date_to_serial(datetime.date(2026, 4, 1))
    clinics = [Clinic("1", 30), Clinic("2", 30)]
    existing = [_make_period("BLK", base, {"1": 30}, {"2": 30}, los=5)]
    study = NewStudyInput(
        protocol="P", male_count=10, female_count=10,
        periods=1, washout_days=0, los=1,
        preferred_checkin_serial=base,
    )
    ss_result = run_all_strategies(study, clinics, existing)[2]
    if ss_result["feasible"]:
        assert ss_result["shift_days"] >= 0
