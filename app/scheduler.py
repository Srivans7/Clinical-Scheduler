"""
Scheduling engine for Clinical Trial Scheduler.

Three strategies:
  1. Shift     – keep participant split, move dates until a free slot is found
  2. Split     – keep preferred dates, split participants across ≤2 clinics
  3. Shift+Split – try split at progressively shifted dates

Constraints enforced:
  C1. Males and Females must NOT share a clinic
  C2. A clinic must not run more than 2 DISTINCT studies simultaneously
  C3. A study must not be split across more than 2 clinics
"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.models import Clinic, StudyPeriod, NewStudyInput, serial_to_date

MAX_SHIFT_DAYS = 120      # search window
MAX_SPLIT_CLINICS = 2     # constraint C3


# ──────────────────────────────────────────────────────────────────────────────
# Availability helpers
# ──────────────────────────────────────────────────────────────────────────────

def _occupied_capacity(clinic_id: str, date_serial: int, periods: List[StudyPeriod]) -> int:
    """Sum of participants already in clinic_id on date_serial."""
    total = 0
    for p in periods:
        if date_serial in p.date_range():
            # Sum male and female counts separately to avoid key collision when
            # both maps contain the same clinic_id (dict merge would lose one value).
            total += p.male_clinic_map.get(clinic_id, 0)
            total += p.female_clinic_map.get(clinic_id, 0)
    return total


def _study_count_in_clinic(clinic_id: str, date_serial: int, periods: List[StudyPeriod],
                            exclude_protocol: Optional[str] = None) -> int:
    """Count distinct studies in clinic_id on date_serial (constraint C2)."""
    protocols = set()
    for p in periods:
        if exclude_protocol and p.protocol == exclude_protocol:
            continue
        if date_serial in p.date_range():
            all_clinics = set(p.male_clinic_map.keys()) | set(p.female_clinic_map.keys())
            if clinic_id in all_clinics:
                protocols.add(p.protocol)
    return len(protocols)


def _get_available_capacity(clinic_id: str, clinics_map: Dict[str, Clinic],
                             checkin: int, checkout: int,
                             periods: List[StudyPeriod]) -> int:
    """Minimum free capacity of clinic across all dates in [checkin, checkout]."""
    clinic = clinics_map.get(clinic_id)
    if not clinic:
        return 0
    min_free = clinic.capacity
    for day in range(checkin, checkout + 1):
        occupied = _occupied_capacity(clinic_id, day, periods)
        free = clinic.capacity - occupied
        min_free = min(min_free, free)
    return max(0, min_free)


def _clinic_can_accept_study(clinic_id: str, checkin: int, checkout: int,
                              periods: List[StudyPeriod], new_protocol: str) -> bool:
    """Check C2: clinic must not already have 2 studies during [checkin, checkout]."""
    for day in range(checkin, checkout + 1):
        if _study_count_in_clinic(clinic_id, day, periods, exclude_protocol=new_protocol) >= 2:
            return False
    return True


def _free_beds_by_clinic_on_day(clinics: List[Clinic], day: int,
                                periods: List[StudyPeriod]) -> Dict[str, int]:
    """Free beds per clinic on a given date (capacity - occupied)."""
    free_map: Dict[str, int] = {}
    for clinic in clinics:
        occupied = _occupied_capacity(clinic.clinic_id, day, periods)
        free_map[clinic.clinic_id] = max(0, clinic.capacity - occupied)
    return free_map


# ──────────────────────────────────────────────────────────────────────────────
# Clinic allocation helpers
# ──────────────────────────────────────────────────────────────────────────────

def _find_single_clinic(count: int, gender_exclusion_clinics: set,
                        clinics: List[Clinic], clinics_map: Dict[str, Clinic],
                        checkin: int, checkout: int,
                        periods: List[StudyPeriod], new_protocol: str) -> Optional[Dict[str, int]]:
    """Try to fit `count` participants in one clinic (no same-gender conflict, C2 respected)."""
    for clinic in clinics:
        if clinic.clinic_id in gender_exclusion_clinics:
            continue
        if not _clinic_can_accept_study(clinic.clinic_id, checkin, checkout, periods, new_protocol):
            continue
        free = _get_available_capacity(clinic.clinic_id, clinics_map, checkin, checkout, periods)
        if free >= count:
            return {clinic.clinic_id: count}
    return None


def _find_split_clinics(count: int, gender_exclusion_clinics: set,
                         clinics: List[Clinic], clinics_map: Dict[str, Clinic],
                         checkin: int, checkout: int,
                         periods: List[StudyPeriod], new_protocol: str) -> Optional[Dict[str, int]]:
    """Try to fit `count` participants across exactly 2 clinics (C3 respected)."""
    eligible = [
        c for c in clinics
        if c.clinic_id not in gender_exclusion_clinics
        and _clinic_can_accept_study(c.clinic_id, checkin, checkout, periods, new_protocol)
        and _get_available_capacity(c.clinic_id, clinics_map, checkin, checkout, periods) > 0
    ]
    for i, c1 in enumerate(eligible):
        cap1 = _get_available_capacity(c1.clinic_id, clinics_map, checkin, checkout, periods)
        if cap1 <= 0:
            continue
        for c2 in eligible[i + 1:]:
            cap2 = _get_available_capacity(c2.clinic_id, clinics_map, checkin, checkout, periods)
            if cap1 + cap2 >= count:
                # allocate as much as possible to c1, remainder to c2
                alloc1 = min(cap1, count)
                alloc2 = count - alloc1
                if alloc2 <= cap2:
                    return {c1.clinic_id: alloc1, c2.clinic_id: alloc2}
    return None


def _allocate_gender(count: int, gender_exclusion_clinics: set,
                      clinics: List[Clinic], clinics_map: Dict[str, Clinic],
                      checkin: int, checkout: int,
                      periods: List[StudyPeriod], new_protocol: str,
                      allow_split: bool) -> Optional[Dict[str, int]]:
    if count == 0:
        return {}
    result = _find_single_clinic(count, gender_exclusion_clinics, clinics, clinics_map,
                                  checkin, checkout, periods, new_protocol)
    if result:
        return result
    if allow_split:
        return _find_split_clinics(count, gender_exclusion_clinics, clinics, clinics_map,
                                    checkin, checkout, periods, new_protocol)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Period scheduling
# ──────────────────────────────────────────────────────────────────────────────

def _try_schedule_at(checkin: int, study: NewStudyInput,
                      clinics: List[Clinic], clinics_map: Dict[str, Clinic],
                      periods: List[StudyPeriod],
                      allow_split: bool) -> Optional[List[dict]]:
    """
    Try to schedule all `study.periods` starting at `checkin`.
    Returns list of period dicts if successful, else None.
    """
    result_periods = []
    current_checkin = checkin

    for period_num in range(1, study.periods + 1):
        checkout = current_checkin + study.los - 1

        # Constraint C1: males and females must not share a clinic.
        # Try males-first; if that leaves no room for females, retry females-first.
        # This ensures the preferred date is honoured even when clinic ordering
        # would otherwise cause an unnecessary failure.

        male_alloc = _allocate_gender(
            count=study.male_count,
            gender_exclusion_clinics=set(),
            clinics=clinics,
            clinics_map=clinics_map,
            checkin=current_checkin,
            checkout=checkout,
            periods=periods,
            new_protocol=study.protocol,
            allow_split=allow_split,
        )
        female_alloc: Optional[Dict[str, int]] = None
        if male_alloc is not None:
            female_alloc = _allocate_gender(
                count=study.female_count,
                gender_exclusion_clinics=set(male_alloc.keys()),  # C1
                clinics=clinics,
                clinics_map=clinics_map,
                checkin=current_checkin,
                checkout=checkout,
                periods=periods,
                new_protocol=study.protocol,
                allow_split=allow_split,
            )

        # Retry with females-first when both genders are present and the
        # males-first order left no valid clinic for females.
        if female_alloc is None and study.male_count > 0 and study.female_count > 0:
            female_retry = _allocate_gender(
                count=study.female_count,
                gender_exclusion_clinics=set(),
                clinics=clinics,
                clinics_map=clinics_map,
                checkin=current_checkin,
                checkout=checkout,
                periods=periods,
                new_protocol=study.protocol,
                allow_split=allow_split,
            )
            if female_retry is not None:
                male_retry = _allocate_gender(
                    count=study.male_count,
                    gender_exclusion_clinics=set(female_retry.keys()),  # C1
                    clinics=clinics,
                    clinics_map=clinics_map,
                    checkin=current_checkin,
                    checkout=checkout,
                    periods=periods,
                    new_protocol=study.protocol,
                    allow_split=allow_split,
                )
                if male_retry is not None:
                    male_alloc = male_retry
                    female_alloc = female_retry

        if male_alloc is None or female_alloc is None:
            return None

        period_dict = {
            "period_num": period_num,
            "checkin_serial": current_checkin,
            "checkout_serial": checkout,
            "checkin_date": serial_to_date(current_checkin).strftime("%Y-%m-%d"),
            "checkout_date": serial_to_date(checkout).strftime("%Y-%m-%d"),
            "male_clinics": male_alloc,
            "female_clinics": female_alloc,
        }
        result_periods.append(period_dict)

        # advance checkin for next period: checkout + washout days
        current_checkin = checkout + study.washout_days

    return result_periods


# ──────────────────────────────────────────────────────────────────────────────
# Public strategy functions
# ──────────────────────────────────────────────────────────────────────────────

def _build_clinics_map(clinics: List[Clinic]) -> Dict[str, Clinic]:
    return {c.clinic_id: c for c in clinics}


def strategy_shift(study: NewStudyInput, clinics: List[Clinic],
                   periods: List[StudyPeriod]) -> dict:
    """
    Strategy 1 – Shift: evaluate only preferred check-in date with no split.
    Returns result dict.
    """
    clinics_map = _build_clinics_map(clinics)
    base = study.preferred_checkin_serial
    result = _try_schedule_at(base, study, clinics, clinics_map, periods, allow_split=False)
    free_map = _free_beds_by_clinic_on_day(clinics, base, periods)

    if result is not None:
        return {
            "strategy": "Shift",
            "feasible": True,
            "shift_days": 0,
            "periods": result,
            "note": "No shift needed.",
            "evaluated_checkin_date": serial_to_date(base).strftime("%Y-%m-%d"),
            "checkin_beds_free": free_map,
            "checkin_beds_free_total": sum(free_map.values()),
        }

    return {
        "strategy": "Shift",
        "feasible": False,
        "note": "Not feasible on preferred check-in without split.",
        "evaluated_checkin_date": serial_to_date(base).strftime("%Y-%m-%d"),
        "checkin_beds_free": free_map,
        "checkin_beds_free_total": sum(free_map.values()),
    }


def strategy_split(study: NewStudyInput, clinics: List[Clinic],
                   periods: List[StudyPeriod]) -> dict:
    """
    Strategy 2 – Split: keep preferred dates, split across ≤2 clinics per gender.
    """
    clinics_map = _build_clinics_map(clinics)
    checkin = study.preferred_checkin_serial
    free_map = _free_beds_by_clinic_on_day(clinics, checkin, periods)

    result = _try_schedule_at(checkin, study, clinics, clinics_map, periods, allow_split=True)
    if result is not None:
        return {
            "strategy": "Split",
            "feasible": True,
            "shift_days": 0,
            "periods": result,
            "note": "Scheduled at preferred check-in with split across clinics.",
            "evaluated_checkin_date": serial_to_date(checkin).strftime("%Y-%m-%d"),
            "checkin_beds_free": free_map,
            "checkin_beds_free_total": sum(free_map.values()),
        }
    return {
        "strategy": "Split",
        "feasible": False,
        "note": "Not feasible at preferred check-in even with split.",
        "evaluated_checkin_date": serial_to_date(checkin).strftime("%Y-%m-%d"),
        "checkin_beds_free": free_map,
        "checkin_beds_free_total": sum(free_map.values()),
    }


def strategy_shift_split(study: NewStudyInput, clinics: List[Clinic],
                          periods: List[StudyPeriod]) -> dict:
    """
    Strategy 3 – Shift + Split: shift dates and allow split.
    """
    clinics_map = _build_clinics_map(clinics)
    base = study.preferred_checkin_serial

    for offset in range(MAX_SHIFT_DAYS + 1):
        checkin = base + offset
        result = _try_schedule_at(checkin, study, clinics, clinics_map, periods, allow_split=True)
        if result is not None:
            free_map = _free_beds_by_clinic_on_day(clinics, checkin, periods)
            return {
                "strategy": "Shift+Split",
                "feasible": True,
                "shift_days": offset,
                "periods": result,
                "note": f"Shifted {offset} day(s) and split participants across clinics." if offset else "Split only — no shift needed.",
                "evaluated_checkin_date": serial_to_date(checkin).strftime("%Y-%m-%d"),
                "checkin_beds_free": free_map,
                "checkin_beds_free_total": sum(free_map.values()),
            }

    preferred_free_map = _free_beds_by_clinic_on_day(clinics, base, periods)
    return {
        "strategy": "Shift+Split",
        "feasible": False,
        "note": f"No feasible slot found within {MAX_SHIFT_DAYS} days even with splits.",
        "evaluated_checkin_date": serial_to_date(base).strftime("%Y-%m-%d"),
        "checkin_beds_free": preferred_free_map,
        "checkin_beds_free_total": sum(preferred_free_map.values()),
    }


def strategy_shift_alternatives(study: NewStudyInput, clinics: List[Clinic],
                                 periods: List[StudyPeriod]) -> dict:
    """
    Strategy 4 (Optional) – Alternative Shift Dates: show up to 3 future dates
    where a simple shift (no split) would be feasible. For checking/reference only.
    """
    clinics_map = _build_clinics_map(clinics)
    base = study.preferred_checkin_serial
    alternatives = []
    max_alternatives = 3

    for offset in range(1, MAX_SHIFT_DAYS + 1):  # start from offset 1 (skip preferred date)
        if len(alternatives) >= max_alternatives:
            break
        
        checkin = base + offset
        result = _try_schedule_at(checkin, study, clinics, clinics_map, periods, allow_split=False)
        
        if result is not None:
            free_map = _free_beds_by_clinic_on_day(clinics, checkin, periods)
            alternatives.append({
                "checkin_date": serial_to_date(checkin).strftime("%Y-%m-%d"),
                "shift_days": offset,
                "clinics_used": list(set(
                    list(result[0]["male_clinics"].keys()) + 
                    list(result[0]["female_clinics"].keys())
                )),
                "beds_free": sum(free_map.values()),
            })

    return {
        "strategy": "Alternative Shift Dates",
        "optional": True,
        "feasible": len(alternatives) > 0,
        "note": "Optional — reference for available shift dates (simple shift without split).",
        "alternatives": alternatives,
        "alternatives_count": len(alternatives),
        "max_alternatives_shown": max_alternatives,
    }


def diagnose_preferred_date_block(
    study: NewStudyInput,
    clinics: List[Clinic],
    clinics_map: Dict[str, Clinic],
    periods: List[StudyPeriod],
) -> Optional[str]:
    """
    Returns a human-readable explanation of why the preferred check-in date
    cannot be scheduled even with splits.  Returns None when the date is fine.
    """
    checkin = study.preferred_checkin_serial
    checkout = checkin + study.los - 1

    # If split scheduling works at the preferred date, there is no block.
    if _try_schedule_at(checkin, study, clinics, clinics_map, periods, allow_split=True) is not None:
        return None

    c2_blocked: List[str] = []
    free_by_clinic: Dict[str, int] = {}

    for clinic in clinics:
        free_by_clinic[clinic.clinic_id] = _get_available_capacity(
            clinic.clinic_id, clinics_map, checkin, checkout, periods
        )
        if not _clinic_can_accept_study(clinic.clinic_id, checkin, checkout, periods, study.protocol):
            c2_blocked.append(clinic.clinic_id)

    available_ids = [c.clinic_id for c in clinics if c.clinic_id not in c2_blocked]
    free_total = sum(free_by_clinic[cid] for cid in available_ids)
    needed = study.male_count + study.female_count

    parts: List[str] = []

    if len(c2_blocked) == len(clinics):
        parts.append(
            "All clinics already host 2 studies on this date "
            "(constraint: max 2 studies per clinic)"
        )
    elif c2_blocked:
        label = "Clinics" if len(c2_blocked) > 1 else "Clinic"
        verb  = "are" if len(c2_blocked) > 1 else "is"
        parts.append(
            f"{label} {', '.join(c2_blocked)} {verb} locked "
            f"(already hosting 2 studies — max 2 per clinic)"
        )

    if available_ids and free_total < needed:
        parts.append(
            f"Only {free_total} bed{'s' if free_total != 1 else ''} free across "
            f"{len(available_ids)} available clinic{'s' if len(available_ids) != 1 else ''}, "
            f"but {needed} participant{'s' if needed != 1 else ''} need scheduling"
        )
    elif available_ids and free_total >= needed:
        # Capacity exists but gender-separation (C1) makes it impossible.
        parts.append(
            "No valid clinic pair found to place males and females separately "
            "(male/female must not share a clinic)"
        )

    if not parts:
        parts.append("Preferred date is unavailable due to scheduling constraints")

    return "; ".join(parts)


def run_all_strategies(study: NewStudyInput, clinics: List[Clinic],
                        periods: List[StudyPeriod]) -> List[dict]:
    clinics_map = _build_clinics_map(clinics)
    block_reason = diagnose_preferred_date_block(study, clinics, clinics_map, periods)
    results = [
        strategy_shift(study, clinics, periods),
        strategy_split(study, clinics, periods),
        strategy_shift_split(study, clinics, periods),
        strategy_shift_alternatives(study, clinics, periods),  # Optional strategy 4
    ]
    split_is_feasible = bool(results[1].get("feasible")) if len(results) > 1 else False
    for r in results:
        if r.get("feasible") or r.get("optional"):  # skip optional strategies
            continue
        if r.get("strategy") == "Shift" and split_is_feasible:
            r["preferred_date_block_reason"] = (
                "Preferred date requires splitting participants across clinics, "
                "but Shift does not allow split."
            )
        elif block_reason:
            r["preferred_date_block_reason"] = block_reason
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Bed-utilisation helper
# ──────────────────────────────────────────────────────────────────────────────

def compute_utilization(
    clinics: List[Clinic],
    existing_periods: List[StudyPeriod],
    new_study_periods: Optional[List[dict]] = None,
) -> dict:
    """
    Build a date × clinic occupancy grid.

    *existing_periods*  – ``List[StudyPeriod]`` parsed from Excel
    *new_study_periods* – optional list of period dicts as returned by a
                          strategy result (keys: ``checkin_serial``,
                          ``checkout_serial``, ``male_clinics``, ``female_clinics``)

    Returns::

        {
            "dates":         ["2026-01-01", ...],   # only days with any occupancy
            "clinics":       [{"id": "1", "capacity": 60}, ...],
            "grid":          {"2026-01-01": {"1": 30, "2": 0}, ...},
            "totals":        {"2026-01-01": 30},     # row sum
            "clinic_totals": {"1": 180, "2": 90},    # column sum
        }
    """
    clinic_list = [{"id": c.clinic_id, "capacity": c.capacity} for c in clinics]
    empty: dict = {
        "dates": [],
        "clinics": clinic_list,
        "grid": {},
        "totals": {},
        "clinic_totals": {c.clinic_id: 0 for c in clinics},
    }

    all_serials: List[int] = []
    for p in existing_periods:
        all_serials.extend([p.checkin_serial, p.checkout_serial])
    if new_study_periods:
        for p in new_study_periods:
            all_serials.extend([p["checkin_serial"], p["checkout_serial"]])

    if not all_serials:
        return empty

    min_serial = min(all_serials)
    max_serial = max(all_serials)

    grid: dict = {}
    totals: dict = {}
    clinic_totals = {c.clinic_id: 0 for c in clinics}

    for day in range(min_serial, max_serial + 1):
        date_iso = serial_to_date(day).strftime("%Y-%m-%d")
        day_row: dict = {}
        day_total = 0
        for clinic in clinics:
            cid = clinic.clinic_id
            occupied = _occupied_capacity(cid, day, existing_periods)
            if new_study_periods:
                for p in new_study_periods:
                    if p["checkin_serial"] <= day <= p["checkout_serial"]:
                        occupied += p.get("male_clinics", {}).get(cid, 0)
                        occupied += p.get("female_clinics", {}).get(cid, 0)
            day_row[cid] = occupied
            day_total += occupied
            clinic_totals[cid] += occupied
        if day_total > 0:
            grid[date_iso] = day_row
            totals[date_iso] = day_total

    dates = sorted(grid.keys())
    return {
        "dates": dates,
        "clinics": clinic_list,
        "grid": grid,
        "totals": totals,
        "clinic_totals": clinic_totals,
    }
