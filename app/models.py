"""
Data models for the Clinical Trial Scheduler.
Dates are stored as Excel serial integers (days since 1900-01-00).
Helper functions convert between Excel serials and ISO date strings.
"""
from dataclasses import dataclass, field
from typing import Optional
import datetime

EXCEL_EPOCH = datetime.date(1899, 12, 30)


def serial_to_date(serial: int) -> datetime.date:
    """Convert an Excel date serial number to a Python date."""
    return EXCEL_EPOCH + datetime.timedelta(days=int(serial))


def date_to_serial(d: datetime.date) -> int:
    """Convert a Python date to an Excel date serial number."""
    return (d - EXCEL_EPOCH).days


def serial_to_iso(serial: int) -> str:
    return serial_to_date(serial).strftime("%Y-%m-%d")


def iso_to_serial(iso_str: str) -> int:
    d = datetime.date.fromisoformat(iso_str)
    return date_to_serial(d)


@dataclass
class Clinic:
    clinic_id: str      # e.g. "1", "3A", "5B"
    capacity: int


@dataclass
class StudyPeriod:
    """One period of a study occupying a clinic."""
    protocol: str
    period_label: str          # "I", "II", "III" …
    male_count: int
    female_count: int
    male_clinic: str           # raw string e.g. "1 (48)" or "1 (50) + 2 (14)"
    female_clinic: str
    checkin_serial: int
    checkout_serial: int
    planned_wo: Optional[int]
    actual_wo: Optional[int]
    los: int

    # parsed from male_clinic / female_clinic
    male_clinic_map: dict = field(default_factory=dict)   # {clinic_id: count}
    female_clinic_map: dict = field(default_factory=dict)

    def date_range(self):
        """Returns all serial dates occupied (checkin .. checkout inclusive)."""
        return range(self.checkin_serial, self.checkout_serial + 1)


@dataclass
class NewStudyInput:
    protocol: str
    male_count: int
    female_count: int
    periods: int
    washout_days: int
    los: int
    preferred_checkin_serial: int
