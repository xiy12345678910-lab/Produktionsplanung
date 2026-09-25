#!/usr/bin/env python3
"""Server-side feasibility checks for new CNC releases."""
from __future__ import annotations
import os
from datetime import datetime
from zoneinfo import ZoneInfo

# Windows-Python bringt keine IANA-Zeitzonendatenbank mit; ohne das Paket
# "tzdata" schlägt ZoneInfo fehl. Dann wird die Systemzeitzone des Servers
# verwendet (astimezone() ohne Argument, inkl. Sommer-/Winterzeit).
try:
    LOCAL_TZ = ZoneInfo(os.environ.get("MP_TIMEZONE", "Europe/Berlin"))
except Exception:
    try:
        LOCAL_TZ = ZoneInfo("Europe/Berlin")
    except Exception:
        LOCAL_TZ = None

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
                        "machineId":mid,"orderId":oid})
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

def personnel_cover(state,seg):
    if not state.get("personnelGate",False):
        return True,0,0
    req=max(0,int(float(machine(state,seg["machineId"]).get("staffRequired",0) or 0)))
    if req<=0:
        return True,0,0
    dk=seg["start"].strftime("%Y-%m-%d")
    absent={str(a.get("employeeId")) for a in state.get("personnelAbsences") or []
            if isinstance(a,dict) and str(a.get("date"))==dk}
    assignments={(str(a.get("employeeId")),str(a.get("date"))):a
                 for a in state.get("personnelAssignments") or [] if isinstance(a,dict)}
    pieces=[]
    for e in state.get("employees") or []:
        if (not isinstance(e,dict) or not e.get("active",True) or str(e.get("id")) in absent
            or seg["machineId"] not in {str(x) for x in (e.get("skills") or [])}):
            continue
        eid=str(e.get("id"))
        a=assignments.get((eid,dk))
        if not a or str(a.get("machineId"))!=seg["machineId"] or str(a.get("shift"))!=seg["shift"]:
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
            for other in fixed:
                if other["orderId"]==oid or other["machineId"]!=seg["machineId"]:
                    continue
                if overlaps(seg,other):
                    return False,"MP-PLAN-058",f"Freigabe '{name}' kollidiert mit einer festen Maschinenbelegung."
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
