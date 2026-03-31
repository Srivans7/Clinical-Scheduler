# Excel Upload Compatibility Guide

This project accepts only .xlsx files.

## Will a different Excel file run?

Yes, but only when the uploaded file follows the expected structure.

If sheet names or required cells are different, upload may succeed, but parsing or scheduling can fail.

## Required sheet names

The parser expects these exact sheet names:

1. Clinics
2. Existing Sch
3. New Study

## Expected data layout

### 1) Clinics sheet

- Column A: Clinic ID
- Column B: Capacity
- Data starts at first row and continues until Clinic ID is empty.

### 2) Existing Sch sheet

- Row 1 is header.
- Data starts from row 2.
- Expected columns (0-index mapping used by parser):
  - A: Planned WO
  - B: Actual WO
  - C: LOS
  - D: Protocol
  - E: Male
  - F: Female
  - G: Male clinic allocation
  - H: Female clinic allocation
  - I: Period label
  - J: Check-in (Excel serial date)
  - K: Check-out (Excel serial date)

### 3) New Study sheet

Expected values in column C:

- Row 2: Protocol Number
- Row 3: Male
- Row 4: Female
- Row 5: Period
- Row 6: Washout
- Row 7: LOS
- Row 8: Preferred check-in (Excel serial date)

If protocol or preferred check-in is missing, new study input is treated as incomplete.

## Why is my file visible in uploads?

That is expected behavior.

The app saves uploaded files into the uploads folder first, then parses them.

## Common reasons an upload does not work

1. File is not .xlsx.
2. Required sheet name is changed.
3. Required cells are empty.
4. Date fields are not valid Excel serial dates where expected.
5. Numeric fields contain non-numeric text.

## Recommendation

Use one validated template and keep:

- Sheet names unchanged
- Column order unchanged
- Required rows/cells unchanged

This ensures any new upload runs reliably in the scheduler.
