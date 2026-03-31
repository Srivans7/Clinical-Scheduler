# Simple Workflow Explanation (Only Key Points)

## What this system does

This app helps you place a new clinical study into already busy clinics without breaking rules.

## How it works (simple)

1. **Upload Excel**
- The app reads clinics, existing schedules, and new study details.

2. **Validate input**
- It checks required fields like protocol, date, counts, periods, washout, and LOS.
- If input is wrong, it blocks next step and shows errors.

3. **Try 3 scheduling methods**
- `Shift`: move date forward.
- `Split`: keep date, split people across clinics.
- `Shift+Split`: combine both.

4. **Apply constraints every time**
- Male and female cannot share same clinic.
- A clinic cannot run more than 2 studies at the same time.
- One side (male/female) cannot be split into more than 2 clinics.
- Capacity must be enough for full stay days.

5. **Show preview first**
- It shows possible outcomes before final save.
- If user changes input after preview, preview becomes stale and must be regenerated.

6. **Confirm and store**
- Final save happens only after confirmation.
- Data is saved in SQLite with linked IDs.

## Why linked IDs are important

Each confirmed run is traceable with:
- Transaction ID (`TRN-*`)
- File ID (`FIL-*`)
- Input record IDs (`INP-*`)
- Output record ID (`OUT-*`)
- Operation IDs (`OP-*`)

So you can always answer:
- Which file was uploaded?
- What input was used?
- What output was generated?
- When and how it was confirmed?

## Where to check history

- Use the **History** page to search old transactions and see full linked records.
