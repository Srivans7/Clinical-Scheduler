"""
Diagnostic: find which inputs give shift=0 on 2 April 2026.
Run: python check_scenarios.py
"""
import datetime
import sys
sys.path.insert(0, r'c:/Users/Lenovo/Downloads/clinical-scheduler')

from app.models import Clinic, StudyPeriod, NewStudyInput, date_to_serial, serial_to_date
from app.scheduler import run_all_strategies

clinics = [Clinic('1', 60), Clinic('2', 60), Clinic('3', 60)]
preferred = date_to_serial(datetime.date(2026, 4, 2))

# Scenario A: completely empty schedule
study_a = NewStudyInput('NEW-A', male_count=20, female_count=20,
    periods=1, washout_days=0, los=5, preferred_checkin_serial=preferred)
results_a = run_all_strategies(study_a, clinics, [])

# Scenario B: one existing study uses clinic 1 only
existing_b = [StudyPeriod('EX1', 'I', 30, 30, '1 (30)', '2 (30)',
    preferred, preferred + 4, None, None, 5, {'1': 30}, {'2': 30})]
study_b = NewStudyInput('NEW-B', male_count=20, female_count=20,
    periods=1, washout_days=0, los=5, preferred_checkin_serial=preferred)
results_b = run_all_strategies(study_b, clinics, existing_b)

# Scenario C: two studies already in clinic 1 (C2 lock), 2 and 3 free
existing_c = [
    StudyPeriod('EX1', 'I', 10, 0, '1 (10)', '', preferred, preferred + 4, None, None, 5, {'1': 10}, {}),
    StudyPeriod('EX2', 'I', 10, 0, '1 (10)', '', preferred, preferred + 4, None, None, 5, {'1': 10}, {}),
]
study_c = NewStudyInput('NEW-C', male_count=20, female_count=20,
    periods=1, washout_days=0, los=5, preferred_checkin_serial=preferred)
results_c = run_all_strategies(study_c, clinics, existing_c)

# Scenario D: simulate your real case — 2 studies in ALL clinics (everything C2-locked)
existing_d = []
for cid in ['1', '2', '3']:
    existing_d.append(StudyPeriod(f'EX-{cid}-A', 'I', 10, 0, f'{cid} (10)', '',
        preferred, preferred + 3, None, None, 4, {cid: 10}, {}))
    existing_d.append(StudyPeriod(f'EX-{cid}-B', 'I', 10, 0, f'{cid} (10)', '',
        preferred, preferred + 3, None, None, 4, {cid: 10}, {}))
study_d = NewStudyInput('NEW-D', male_count=10, female_count=10,
    periods=1, washout_days=0, los=1, preferred_checkin_serial=preferred)
results_d = run_all_strategies(study_d, clinics, existing_d)

def show(label, existing, study, results):
    SEP = '=' * 60
    print(f'\n{SEP}')
    print(f'SCENARIO {label}')
    print(f'  Preferred date : {serial_to_date(study.preferred_checkin_serial)}')
    print(f'  New Study      : {study.male_count}M / {study.female_count}F  LOS={study.los}  Periods={study.periods}  Washout={study.washout_days}')
    print(f'  Existing rows  : {len(existing)}')
    for r in results:
        status = 'FEASIBLE  ' if r['feasible'] else 'INFEASIBLE'
        shift  = r.get('shift_days', '-')
        note   = r.get('note', '')
        reason = r.get('preferred_date_block_reason', '')
        print(f'  [{r["strategy"]:<12s}] {status}  shift={shift}   {note}')
        if reason:
            print(f'               WHY BLOCKED: {reason}')

show('A  — Empty schedule',           [],         study_a, results_a)
show('B  — 1 study in Clinic 1',      existing_b, study_b, results_b)
show('C  — C2 lock in Clinic 1 only', existing_c, study_c, results_c)
show('D  — C2 lock in ALL clinics',   existing_d, study_d, results_d)

print('\n' + '=' * 60)
print('SUMMARY — inputs that give shift=0 (book on preferred date):')
for label, results, study, existing in [
    ('A', results_a, study_a, []),
    ('B', results_b, study_b, existing_b),
    ('C', results_c, study_c, existing_c),
    ('D', results_d, study_d, existing_d),
]:
    zero_shift = [r['strategy'] for r in results if r['feasible'] and r.get('shift_days', 99) == 0]
    if zero_shift:
        print(f'  Scenario {label}: strategies {zero_shift} give shift=0')
    else:
        min_shift = min((r.get('shift_days', 9999) for r in results if r['feasible']), default='N/A')
        if min_shift == 9999:
            min_shift = 'N/A'
        print(f'  Scenario {label}: NO zero-shift possible — min shift={min_shift}')
