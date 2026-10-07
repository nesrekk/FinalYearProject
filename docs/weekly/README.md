# Weekly reports (2026-27)

One page per week of the live season, "what the models said vs what happened", for the weekly meeting with the guide
(round 9 step 6). Each `<Sunday>.md` covers Monday to Sunday (US Eastern) and is written by

```bash
cd scripts && DB_TARGET=local /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 weekly_report.py
```

after Monday's `daily_update.py` (which runs the Forecast Ledger first, so Sunday's games are scored). The same report,
printable, is the app's Teams › Forecast Ledger › Weekly report tab (`?page=ledger&tab=weekly&wk=<Sunday>`). Every
number comes from the stored tables through `api/weekly_report_lib.py`; see its docstring for the sections and rules.
The first week is 2026-10-19 to 2026-10-25 (opening night is 2026-10-20); paired tests appear from the third week
(100 games scored under every version).
