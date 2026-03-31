from app.excel_parser import parse_excel
from app.models import NewStudyInput, iso_to_serial
from app.scheduler import run_all_strategies

clinics, existing, _ = parse_excel(r'C:\Users\Lenovo\Downloads\Case Study.xlsx')

# Free at 2026-04-02 LOS=3: Clinic4=14, Clinic5B=8, Clinic3A=5 => combined best = 14+8=22
# Set males=18 => no single clinic fits (max free=14), but Clinic4(14)+Clinic5B(4)=18 fits via Split
study = NewStudyInput(
    protocol='B',
    male_count=18,
    female_count=0,
    periods=1,
    washout_days=7,
    los=3,
    preferred_checkin_serial=iso_to_serial('2026-04-02'),
)

results = run_all_strategies(study, clinics, existing)
for r in results:
    print(f"{r['strategy']}: feasible={r['feasible']}  shift_days={r.get('shift_days','-')}")
    if r['feasible'] and r.get('periods'):
        p = r['periods'][0]
        print(f"  male_clinics={p['male_clinics']}  female_clinics={p['female_clinics']}")
