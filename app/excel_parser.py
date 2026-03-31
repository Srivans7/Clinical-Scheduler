"""
Parses Case Study.xlsx (or any compatible file) into in-memory data structures.
Uses only stdlib (zipfile + xml) so no openpyxl dependency is required.
"""
import zipfile
import xml.etree.ElementTree as ET
import re
from typing import List, Dict, Tuple

from app.models import Clinic, StudyPeriod, NewStudyInput, serial_to_date

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS = {"x": NS}


# ---------------------------------------------------------------------------
# low-level helpers
# ---------------------------------------------------------------------------

def _shared_strings(z: zipfile.ZipFile) -> List[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    tree = ET.fromstring(z.read("xl/sharedStrings.xml"))
    result = []
    for si in tree.findall(".//x:si", _NS):
        text = "".join(
            t.text or ""
            for t in si.iter(f"{{{NS}}}t")
        )
        result.append(text)
    return result


def _sheet_files(z: zipfile.ZipFile) -> Dict[str, str]:
    """Returns {sheet_name: zip_path} for all sheets."""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rel_map = {r.attrib["Id"]: r.attrib["Target"] for r in rels}
    result = {}
    for s in wb.findall(
        ".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"
    ):
        rid = s.attrib.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        name = s.attrib["name"]
        target = rel_map.get(rid, "")
        path = f"xl/{target}" if not target.startswith("/") else target.lstrip("/")
        result[name] = path
    return result


def _read_sheet_as_grid(
    z: zipfile.ZipFile, path: str, shared: List[str]
) -> Dict[Tuple[int, int], str]:
    """Returns {(row_idx, col_idx): cell_value_str} (0-based)."""
    tree = ET.fromstring(z.read(path))
    grid = {}
    for row_el in tree.findall(".//x:row", _NS):
        for c in row_el.findall("x:c", _NS):
            ref = c.attrib.get("r", "")
            col_str = re.match(r"([A-Z]+)", ref)
            row_num = re.search(r"(\d+)", ref)
            if not col_str or not row_num:
                continue
            col_idx = _col_to_idx(col_str.group(1))
            row_idx = int(row_num.group(1)) - 1
            t = c.attrib.get("t", "")
            v_el = c.find("x:v", _NS)
            val = ""
            if v_el is not None and v_el.text is not None:
                if t == "s":
                    try:
                        val = shared[int(v_el.text)]
                    except (IndexError, ValueError):
                        val = v_el.text
                else:
                    val = v_el.text
            grid[(row_idx, col_idx)] = val
    return grid


def _col_to_idx(col: str) -> int:
    idx = 0
    for ch in col.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _safe_int(val: str, default=None):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# clinic parser
# ---------------------------------------------------------------------------

def _parse_clinic_allocation(raw: str) -> Dict[str, int]:
    """
    Parse strings like '1 (48)' or '1 (50) + 2 (14)' or '5A (20) + 5B (12)'
    Returns {clinic_id: count}.
    """
    if not raw or not raw.strip():
        return {}
    result = {}
    for part in raw.split("+"):
        part = part.strip()
        m = re.match(r"^(\w+)\s*\((\d+)\)$", part)
        if m:
            result[m.group(1)] = int(m.group(2))
    return result


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def parse_excel(filepath: str):
    """
    Returns (clinics, existing_periods, new_study_input).
    new_study_input may be None if the New Study sheet is incomplete.
    """
    with zipfile.ZipFile(filepath, "r") as z:
        shared = _shared_strings(z)
        sheets = _sheet_files(z)

        clinics = _parse_clinics(z, sheets, shared)
        existing = _parse_existing_schedule(z, sheets, shared)
        new_study = _parse_new_study(z, sheets, shared)

    return clinics, existing, new_study


def _parse_clinics(z, sheets, shared) -> List[Clinic]:
    path = sheets.get("Clinics")
    if not path:
        return []
    grid = _read_sheet_as_grid(z, path, shared)
    clinics = []
    row = 0
    while True:
        cid = grid.get((row, 0), "").strip()
        cap = grid.get((row, 1), "").strip()
        if not cid:
            break
        capacity = _safe_int(cap, 0)
        clinics.append(Clinic(clinic_id=cid, capacity=capacity))
        row += 1
    return clinics


def _parse_existing_schedule(z, sheets, shared) -> List[StudyPeriod]:
    path = sheets.get("Existing Sch")
    if not path:
        return []
    grid = _read_sheet_as_grid(z, path, shared)
    periods = []
    # Row 0 is header; data starts at row 1
    row = 1
    while True:
        protocol = grid.get((row, 3), "").strip()
        if not protocol:
            # check if we've simply passed all data rows
            if row > 200:
                break
            row += 1
            continue

        planned_wo = _safe_int(grid.get((row, 0), ""))
        actual_wo = _safe_int(grid.get((row, 1), ""))
        los = _safe_int(grid.get((row, 2), ""), 1)
        male = _safe_int(grid.get((row, 4), ""), 0)
        female = _safe_int(grid.get((row, 5), ""), 0)
        male_clinic_raw = grid.get((row, 6), "").strip()
        female_clinic_raw = grid.get((row, 7), "").strip()
        period_label = grid.get((row, 8), "").strip()
        checkin = _safe_int(grid.get((row, 9), ""), 0)
        checkout = _safe_int(grid.get((row, 10), ""), 0)

        sp = StudyPeriod(
            protocol=protocol,
            period_label=period_label,
            male_count=male,
            female_count=female,
            male_clinic=male_clinic_raw,
            female_clinic=female_clinic_raw,
            checkin_serial=checkin,
            checkout_serial=checkout,
            planned_wo=planned_wo,
            actual_wo=actual_wo,
            los=los,
            male_clinic_map=_parse_clinic_allocation(male_clinic_raw),
            female_clinic_map=_parse_clinic_allocation(female_clinic_raw),
        )
        periods.append(sp)
        row += 1

    return periods


def _parse_new_study(z, sheets, shared):
    path = sheets.get("New Study")
    if not path:
        return None
    grid = _read_sheet_as_grid(z, path, shared)
    # Layout (0-indexed rows, col B=1, C=2)
    # row1: "Protocol Number" / value
    # row2: "Male" / value
    # row3: "Female" / value
    # row4: "Period" / value
    # row5: "Washout" / value
    # row6: "LOS" / value
    # row7: "Preferred checkin" / value
    try:
        protocol = grid.get((1, 2), "").strip()
        male = _safe_int(grid.get((2, 2), ""), 0)
        female = _safe_int(grid.get((3, 2), ""), 0)
        periods = _safe_int(grid.get((4, 2), ""), 1)
        washout = _safe_int(grid.get((5, 2), ""), 7)
        los = _safe_int(grid.get((6, 2), ""), 1)
        checkin = _safe_int(grid.get((7, 2), ""), 0)
        if not protocol or checkin == 0:
            return None
        return NewStudyInput(
            protocol=protocol,
            male_count=male,
            female_count=female,
            periods=periods,
            washout_days=washout,
            los=los,
            preferred_checkin_serial=checkin,
        )
    except Exception:
        return None
