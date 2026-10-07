#!/usr/bin/env python3
"""Server-side feasibility checks for new CNC releases."""
from __future__ import annotations
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Windows-Python bringt keine IANA-Zeitzonendatenbank mit; ohne das Paket
# "tzdata" schlägt ZoneInfo fehl. Dann wird die Systemzeitzone des Servers
# verwendet (astimezone() ohne Argument, inkl. Sommer-/Winterzeit).
def _config_timezone():
    """V12.15.0: locale.timezone aus config/firma.json (MP_CONFIG_DIR bzw. <Programmordner>/config), sonst ''."""
    try:
        import json
        from pathlib import Path
        d = Path(os.environ.get("MP_CONFIG_DIR") or (Path(__file__).resolve().parent / "config"))
        tz = json.loads((d / "firma.json").read_text(encoding="utf-8-sig")).get("locale", {}).get("timezone", "")
        return tz if isinstance(tz, str) else ""
    except Exception:
        return ""


def _pick_tz():
    # Vorrang: Umgebung MP_TIMEZONE > Config locale.timezone > Europe/Berlin
    for name in (os.environ.get("MP_TIMEZONE", ""), _config_timezone(), "Europe/Berlin"):
        if not name:
            continue
        try:
            return ZoneInfo(name)
        except Exception:
            continue
    return None


LOCAL_TZ = _pick_tz()

def parse_dt(value):
    if not isinstance(value, str) or not value.strip():
        return None
    raw=value.strip()
    if raw.endswith("Z"):
        raw=raw[:-1]+"+00:00"
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None

def local_dt(value):
    dt=parse_dt(value)
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt
    return (dt.astimezone(LOCAL_TZ) if LOCAL_TZ else dt.astimezone()).replace(tzinfo=None)

def pair(a,z):
    a,z=local_dt(a),local_dt(z)
    return (a,z) if a and z and z>a else None

def clock_minutes(v):
    if not isinstance(v,str) or len(v)!=5 or v[2]!=":":
        return None
    try:
        h,m=int(v[:2]),int(v[3:])
    except ValueError:
        return None
    return h*60+m if 0<=h<=23 and 0<=m<=59 else None

def on_day(day,v):
    mins=clock_minutes(v)
    if mins is None:
        return None
    return day.replace(hour=mins//60,minute=mins%60,second=0,microsecond=0)

def machine(state,mid):
    return next((m for m in state.get("machines") or []
                 if isinstance(m,dict) and str(m.get("id"))==mid),{})

def dept_of(state,mid):
    return str(machine(state,mid).get("departmentId") or "cnc")

def mode_for_day(state,mid,day):
    key=day.strftime("%Y-%m-%d")
    for x in state.get("exceptions") or []:
        if isinstance(x,dict) and str(x.get("date"))==key:
            return str(x.get("mode","0"))
    if day.weekday()>=5:
        return "0"
    iso=day.isocalendar()
    for x in state.get("weekRules") or []:
        if (isinstance(x,dict) and str(x.get("machineId"))==mid
            and int(x.get("year",-1))==iso.year
            and int(x.get("week",-1))==iso.week):
            return str(x.get("mode","0"))
    for x in state.get("yearRules") or []:
        if (isinstance(x,dict) and str(x.get("machineId"))==mid
            and int(x.get("year",-1))==day.year):
            return str(x.get("mode","0"))
    return str(machine(state,mid).get("defaultShiftMode","1"))

def template_intervals(day,t,shift):
    a,z=on_day(day,str(t.get("start",""))),on_day(day,str(t.get("end","")))
    if not a or not z or z<=a:
        return []
    breaks=[]
    for b in t.get("breaks") or []:
        if not isinstance(b,dict) or (not b.get("start") and not b.get("end")):
            continue
        x,y=on_day(day,str(b.get("start",""))),on_day(day,str(b.get("end","")))
        if x and y and y>x:
            breaks.append((x,y))
    breaks.sort()
    out=[]; cur=a
    for x,y in breaks:
        if x>cur:
            out.append((cur,min(x,z),shift))
        cur=max(cur,y)
    if cur<z:
        out.append((cur,z,shift))
    return [(x,y,s) for x,y,s in out if y>x]

def work_intervals(state,mid,day):
    mode=mode_for_day(state,mid,day)
    t=state.get("shiftTemplates") or {}
    if mode=="0":
        return []
    if mode=="1":
        key="fridaySingle" if day.weekday()==4 else "single"
        return template_intervals(day,t.get(key) or {},"single")
    if mode=="2":
        return sorted(template_intervals(day,t.get("early") or {},"early")
                      +template_intervals(day,t.get("late") or {},"late"))
    return []

def make_segments(raw,mid,oid):
    out=[]
    for s in raw or []:
        q=pair(s.get("start"),s.get("end")) if isinstance(s,dict) else None
        if q:
            out.append({"start":q[0],"end":q[1],"shift":str(s.get("shift") or ""),
                        "machineId":mid,"orderId":oid,"laneIndex":s.get("laneIndex"),"crew":s.get("crew")})
    return out

def baseline_segments(order):
    b=order.get("baselinePlan") or {}
    mid=str(b.get("machineId") or order.get("machineId") or "")
    return make_segments(b.get("segments"),mid,str(order.get("id") or ""))

def fixed_segments(state):
    out=[]
    for h in state.get("history") or []:
        if not isinstance(h,dict) or (h.get("recordType") or "done")!="done":
            continue
        raw=h.get("actualSegments") or h.get("plannedSegments") or []
        out+=make_segments(raw,str(h.get("machineId") or ""),"history:"+str(h.get("id") or ""))
    for o in state.get("workSteps") or []:
        if not isinstance(o,dict) or o.get("planningType")!="MACHINE":
            continue
        status=str(o.get("status","planned"))
        if status=="released":
            out+=baseline_segments(o)
        elif status in {"running","paused"}:
            out+=make_segments(o.get("lockedSegments"),str(o.get("machineId") or ""),str(o.get("id") or ""))
    return out

def overlaps(a,b):
    return a["start"]<b["end"] and a["end"]>b["start"]

def machine_lanes(state,mid):
    """Parallelplätze einer Maschine/Linie (V12.8.1): wie viele Aufträge gleichzeitig laufen dürfen."""
    try:
        n=int(float(machine(state,mid).get("lanes") or 1))
    except (TypeError,ValueError):
        n=1
    return max(1,min(20,n))

def max_parallel(seg,others):
    """Höchste Zahl gleichzeitig aktiver Segmente aus others innerhalb von seg."""
    rel=[x for x in others if overlaps(seg,x)]
    if not rel:
        return 0
    points=sorted({seg["start"],seg["end"],*[x["start"] for x in rel],*[x["end"] for x in rel]})
    best=0
    for i in range(len(points)-1):
        a,z=points[i],points[i+1]
        if z<=a or z<=seg["start"] or a>=seg["end"]:
            continue
        mid=a+(z-a)/2
        best=max(best,sum(1 for x in rel if x["start"]<=mid<x["end"]))
    return best

def segment_in_calendar(state,seg):
    if seg["start"].date()!=seg["end"].date():
        return False
    return any(seg["shift"]==shift and seg["start"]>=a and seg["end"]<=z
               for a,z,shift in work_intervals(state,seg["machineId"],seg["start"]))

def hits_machine_block(state,seg):
    for b in state.get("machineBlocks") or []:
        if not isinstance(b,dict) or str(b.get("machineId"))!=seg["machineId"]:
            continue
        q=pair(b.get("start"),b.get("end"))
        if q and seg["start"]<q[1] and seg["end"]>q[0]:
            return True
    return False

# --- Personalzuordnung wie in der Oberflaeche (index.html: personnelAssignment) ---
# Ohne ausdrueckliche Einteilung gilt die Stammmaschine des Mitarbeiters. Vorher zaehlte der
# Server nur ausdrueckliche Einteilungen; die Oberflaeche plante dagegen mit der Stammmaschine,
# so dass Freigaben mit MP-PERS-033 (0/x) abgelehnt wurden, obwohl der Plan besetzt war.
def normalize_mode(v):
    v=str("1" if v is None else v)
    if v=="early":
        return "1"
    if v=="late":
        return "2"
    return v if v in {"0","1","2"} else "1"

def temp_available(e,dk):
    if not isinstance(e,dict) or e.get("employmentType")!="temporary" or not e.get("tempStatus"):
        return True
    return (e.get("tempStatus")=="approved"
            and (not e.get("tempFrom") or dk>=str(e.get("tempFrom")))
            and (not e.get("tempTo") or dk<=str(e.get("tempTo"))))

def is_absent(state,e,dk):
    if not temp_available(e,dk):
        return True
    eid=str(e.get("id"))
    return any(isinstance(a,dict) and str(a.get("employeeId"))==eid and str(a.get("date"))==dk
               for a in state.get("personnelAbsences") or [])

def effective_dept(state,e,day):
    wk=(day-timedelta(days=day.weekday())).strftime("%Y-%m-%d")
    eid=str(e.get("id"))
    for x in state.get("weeklyEmployeeDeployments") or []:
        if isinstance(x,dict) and str(x.get("employeeId"))==eid and str(x.get("weekStart"))==wk:
            return str(x.get("departmentId") or e.get("departmentId") or "")
    return str(e.get("departmentId") or "")

def can_staff(state,e,mid,day):
    return (effective_dept(state,e,day)==dept_of(state,mid)
            and mid in {str(x) for x in e.get("skills") or []})


def daily_hours(e,day):
    days=e.get("workingDays",[1,2,3,4,5])
    if day.isoweekday() not in days:
        return 0.0
    values=e.get("dailyHours") or {}
    return float(values.get(str(day.isoweekday()),float(e.get("weeklyHours",40))/max(1,len(days))))


def limit_assignment(e,a,day):
    if not a:
        return None
    limit=daily_hours(e,day)*60
    start,end=clock_minutes(a.get("start")),clock_minutes(a.get("end"))
    if limit<=0 or start is None or end is None:
        return None
    breaks=[(clock_minutes(b.get("start")),clock_minutes(b.get("end"))) for b in a.get("breaks") or []]
    used=0;stop=start
    for minute in range(start,end):
        if not any(x is not None and y is not None and x<=minute<y for x,y in breaks):
            if used>=limit:
                break
            used+=1
        stop=minute+1
    return {**a,"end":f"{stop//60:02d}:{stop%60:02d}","breaks":[b for b in a.get("breaks") or [] if clock_minutes(b.get("end")) is not None and clock_minutes(b["end"])<=stop]}


def explicit_assignment(state,eid,dk):
    return next((a for a in state.get("personnelAssignments") or []
                 if isinstance(a,dict) and str(a.get("employeeId"))==eid and str(a.get("date"))==dk),None)

def home_machine_for(state,e,day,dk):
    hm=str(e.get("homeMachineId") or "")
    if (not e.get("active",True) or not hm or is_absent(state,e,dk)
            or hm not in {str(x) for x in (e.get("skills") or [])}):
        return None
    m=machine(state,hm)
    if not m or effective_dept(state,e,day)!=str(m.get("departmentId") or "cnc"):
        return None
    return m

def auto_home_shift(state,e,day,dk,m):
    mid=str(m.get("id"))
    req=max(1,int(float(m.get("staffRequired",0) or 0)))
    n={"early":0,"late":0}
    for a in state.get("personnelAssignments") or []:
        if isinstance(a,dict) and str(a.get("date"))==dk and str(a.get("machineId"))==mid and a.get("shift") in n:
            n[a.get("shift")]+=1
    peers=[x for x in state.get("employees") or []
           if isinstance(x,dict) and str(x.get("homeMachineId") or "")==mid
           and home_machine_for(state,x,day,dk) is not None
           and not explicit_assignment(state,str(x.get("id")),dk)]
    for x in peers:
        if x.get("homeShift") in n:
            n[x.get("homeShift")]+=1
    for x in peers:
        if x.get("homeShift") in n:
            continue
        s="early" if n["early"]<req else "late" if n["late"]<req else ("late" if n["late"]<n["early"] else "early")
        if str(x.get("id"))==str(e.get("id")):
            return s
        n[s]+=1
    return "early"

def home_assignment(state,e,day,dk):
    m=home_machine_for(state,e,day,dk)
    if not m:
        return None
    mode=normalize_mode(mode_for_day(state,str(m.get("id")),day))
    if mode=="0":
        return None
    if mode=="1":
        shift="single"
    else:
        shift=e.get("homeShift") if e.get("homeShift") in {"early","late"} else auto_home_shift(state,e,day,dk,m)
    ts=state.get("shiftTemplates") or {}
    t=(ts.get("fridaySingle") if day.weekday()==4 else ts.get("single")) if shift=="single" else ts.get(shift)
    t=t or {}
    return {"employeeId":str(e.get("id")),"date":dk,"machineId":str(m.get("id")),"shift":shift,"laneIndex":int(e.get("homeLaneIndex",1)),
            "start":str(t.get("start","")),"end":str(t.get("end","")),"breaks":t.get("breaks") or []}

def personnel_assignment(state,e,day,dk):
    return limit_assignment(e,explicit_assignment(state,str(e.get("id")),dk) or home_assignment(state,e,day,dk),day)

def personnel_cover(state,seg):
    if not state.get("personnelGate",False):
        return True,0,0
    m=machine(state,seg["machineId"])
    lane=seg.get("laneIndex")
    req=max(0,int(float((m.get("laneStaff") or {}).get(str(lane),m.get("staffRequired",0)) or 0)))
    if m.get("kind")=="line" or m.get("effortScaling"):
        req=max(1,req)
    if seg.get("crew"):
        req=max(req,int(seg["crew"]))
    if req<=0:
        return True,0,0
    day=seg["start"]
    dk=day.strftime("%Y-%m-%d")
    pieces=[]
    for e in state.get("employees") or []:
        if (not isinstance(e,dict) or not e.get("active",True) or is_absent(state,e,dk)
            or not can_staff(state,e,seg["machineId"],day)):
            continue
        eid=str(e.get("id"))
        a=personnel_assignment(state,e,day,dk)
        if not a or str(a.get("machineId"))!=seg["machineId"] or str(a.get("shift"))!=seg["shift"] or (lane is not None and a.get("laneIndex",1)!=lane):
            continue
        x,y=on_day(seg["start"],str(a.get("start",""))),on_day(seg["start"],str(a.get("end","")))
        if not x or not y or y<=x:
            continue
        ep=[(max(x,seg["start"]),min(y,seg["end"]))]
        for b in a.get("breaks") or []:
            if not isinstance(b,dict) or (not b.get("start") and not b.get("end")):
                continue
            bs,be=on_day(seg["start"],str(b.get("start",""))),on_day(seg["start"],str(b.get("end","")))
            if not bs or not be or be<=bs:
                continue
            nxt=[]
            for u,v in ep:
                if be<=u or bs>=v:
                    nxt.append((u,v))
                else:
                    if bs>u:
                        nxt.append((u,min(bs,v)))
                    if be<v:
                        nxt.append((max(be,u),v))
            ep=nxt
        pieces += [(u,v,eid) for u,v in ep if v>u]
    points=sorted({seg["start"],seg["end"],*[x for p in pieces for x in p[:2]]})
    minimum=10**9
    for i in range(len(points)-1):
        a,z=points[i],points[i+1]
        if z<=a or z<=seg["start"] or a>=seg["end"]:
            continue
        mid=a+(z-a)/2
        count=len({eid for x,y,eid in pieces if x<=mid<y})
        minimum=min(minimum,count)
    if minimum==10**9:
        minimum=0
    return minimum>=req,minimum,req

def release_candidates(old,new):
    prior={str(o.get("id")):o for o in old.get("workSteps") or []
           if isinstance(o,dict) and o.get("planningType")=="MACHINE" and o.get("id")}
    out=[]
    for o in new.get("workSteps") or []:
        if (not isinstance(o,dict) or o.get("planningType")!="MACHINE"
            or str(o.get("status","planned"))!="released"):
            continue
        before=prior.get(str(o.get("id")))
        if not before or str(before.get("status","planned"))=="planned":
            out.append(o)
    return out

def validate_release_feasibility(old,new):
    candidates=release_candidates(old,new)
    if not candidates:
        return True,"",""
    fixed=fixed_segments(new)
    caps=new.get("operatorCapacity") or {}
    for o in candidates:
        oid=str(o.get("id") or "")
        name=o.get("order") or oid
        segs=baseline_segments(o)
        for seg in segs:
            if not segment_in_calendar(new,seg):
                return False,"MP-PLAN-056",f"Freigabe '{name}' liegt außerhalb von Schicht/Kalender oder über einer Pause."
            if hits_machine_block(new,seg):
                return False,"MP-PLAN-057",f"Freigabe '{name}' kollidiert mit einer Maschinensperre."
            # V12.8.1: Maschinen/Linien mit Parallelplätzen dürfen bis zu 'lanes' Aufträge gleichzeitig fahren.
            same=[x for x in fixed if x["orderId"]!=oid and x["machineId"]==seg["machineId"]]
            if max_parallel(seg,same)+1>machine_lanes(new,seg["machineId"]):
                return False,"MP-PLAN-058",f"Freigabe '{name}' kollidiert mit einer festen Maschinenbelegung."
            lane=seg.get("laneIndex") or o.get("laneIndex")
            seg["laneIndex"]=lane
            if lane is not None and any(x.get("laneIndex")==lane and overlaps(seg,x) for x in same):
                return False,"MP-PERS-034","Parallelplatz ist bereits belegt."
            ok,count,req=personnel_cover(new,seg)
            if not ok:
                return False,"MP-PERS-033",f"Freigabe '{name}' ist personell unterdeckt ({count}/{req})."

        # Bedienerkapazität ist eine CNC-Ressource; andere Bereiche planen über Besetzung/Linien.
        if dept_of(new,str((o.get("baselinePlan") or {}).get("machineId") or o.get("machineId") or ""))!="cnc":
            continue
        relevant=[x for x in fixed if x["orderId"]!=oid and dept_of(new,x["machineId"])=="cnc"]+segs
        points=sorted({x["start"] for x in relevant}|{x["end"] for x in relevant})
        for i in range(len(points)-1):
            a,z=points[i],points[i+1]
            if z<=a:
                continue
            mid=a+(z-a)/2
            active=[x for x in relevant if x["start"]<=mid<x["end"]]
            if not any(x.get("orderId")==oid for x in active):
                continue
            limits=[int(float(caps.get(x.get("shift"),1) or 1)) for x in active]
            cap=min(limits) if limits else 1
            if len(active)>cap:
                return False,"MP-PLAN-059",f"Freigabe '{name}' überschreitet die Bedienerkapazität ({len(active)}/{cap})."
    return True,"",""
