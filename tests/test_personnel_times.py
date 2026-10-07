#!/usr/bin/env python3
"""Regression checks for net shift hours and interval based personnel limits."""
from datetime import datetime, timedelta
import copy, json, tempfile
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_gates as gates
import server


def net_hours(a):
    start, end = gates.clock_minutes(a['start']), gates.clock_minutes(a['end'])
    return (end - start - sum(gates.clock_minutes(b['end']) - gates.clock_minutes(b['start']) for b in a['breaks'])) / 60


def check(ok, label):
    print(('PASS ' if ok else 'FAIL ') + label)
    if not ok:
        raise SystemExit(1)


def main():
    e = {'id': 'e', 'weeklyHours': 40, 'workingDays': [1, 2, 3, 4, 5]}
    monday = datetime(2026, 10, 5)
    friday = datetime(2026, 10, 9)
    a = gates.limit_assignment(e, {'start': '06:30', 'end': '16:00', 'breaks': [{'start':'09:00','end':'09:15'}, {'start':'12:00','end':'12:30'}]}, monday)
    f = gates.limit_assignment(e, {'start': '06:30', 'end': '11:45', 'breaks': [{'start':'09:00','end':'09:15'}]}, friday)
    check(net_hours(a) == 8.75 and a['end'] == '16:00', 'Mo–Do ergeben 8,75 Netto-Stunden bis 16:00')
    check(net_hours(f) == 5 and f['end'] == '11:45', 'Freitag ergibt 5 Netto-Stunden bis 11:45')
    check(sum(gates.daily_hours(e, monday + timedelta(days=i)) for i in range(5)) == 40, 'Standardwoche ergibt exakt 40 Stunden')
    e.update(weeklyHours=20)
    check(gates.daily_hours(e, monday) == 4 and gates.daily_hours(e, friday) == 4, 'Teilzeit wird nach Standardtagen proportional verteilt')
    e.update(workingDays=[1,3,5], dailyHours={})
    check(abs(sum(gates.daily_hours(e,monday+timedelta(days=i)) for i in range(5))-20)<0.001, 'Individuelle Arbeitstage erhalten die vereinbarten Wochenstunden')
    e.update(workingDays=[1,2,3,4,5],dailyHours={})
    e.update(weeklyHours=20, dailyHours={'1': 8}, workingDays=[1,2,3,4,5])
    check(gates.daily_hours(e, monday) == 8 and gates.daily_hours(e, monday + timedelta(days=1)) == 3, 'Explizite Tagesstunden verteilen den Rest auf freie Tageswerte')
    tmp=tempfile.mkdtemp(prefix='mp-personnel-times-');server.DATA_DIR=Path(tmp);server.DB_PATH=Path(tmp)/'state.sqlite3';server.PBKDF2_ITERS=1000;server.init_db(seed='werbetechnik')
    with server.db_session() as con: base=json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()[0])
    new=copy.deepcopy(base);m=new['machines'][0];new['employees']=[{'id':'emp_time','name':'Zeittest','departmentId':m['departmentId'],'skills':[m['id']],'weeklyHours':40,'active':True,'standardPersonnelTimes':{'single':{'name':'Custom','start':'07:00','end':'15:00','breaks':[{'start':'10:00','end':'10:15'},{'start':'12:00','end':'12:30'}]},'fridaySingle':{'name':'Custom','start':'07:00','end':'11:45','breaks':[{'start':'09:00','end':'09:15'},{'start':'','end':''}]}}}]
    ok,code,_=server.validate_state(base,new);check(ok, 'Server akzeptiert gültige dauerhafte Mitarbeiterzeiten')
    bad=copy.deepcopy(new);bad['employees'][0]['standardPersonnelTimes']['single']['breaks'][0]={'start':'15:15','end':'15:30'}
    ok,code,_=server.validate_state(base,bad);check(not ok and code=='MP-PERS-035', 'Server weist Pausen außerhalb der Stammzeit ab')
    print('8/8 bestanden')


if __name__ == '__main__':
    main()
