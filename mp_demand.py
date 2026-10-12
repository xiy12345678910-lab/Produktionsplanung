"""#73 Phase 1: Bedarf (Rahmenauftraege, Abrufe, Bestand, Reservierung, Bedarf -> FA) als eigenes Modul.

Flach neben server.py, reine Verschiebung ohne Verhaltensaenderung. server.py bietet alle Namen weiter an.
Gemeinsame Helfer (Fehlerklasse der Runtime, Datums-/Zahlenpruefung, Uhrzeit) kommen ueber `host` aus server.py
und werden bei jedem Aufruf dort nachgeschlagen: Tests, die server.now_iso ersetzen, wirken also auch hier.
"""
import secrets

host = None  # server-Modul; setzt server.py beim Import (mp_demand.host = sys.modules[__name__])


def ProductionError(code, message, status=400):
    return host.ProductionError(code, message, status)


def now_iso():
    return host.now_iso()


def _valid_date_key(value):
    return host._valid_date_key(value)


def _finite_float(value):
    return host._finite_float(value)


def _nonnegative_int(value):
    return host._nonnegative_int(value)

# ---------------------------------------------------------------------------------------------
# V12.22.0 Block E: Rahmenaufträge, Abrufe, Bestand, Reservierung und Bedarf → FA.
# Server-autoritativ wie die Produktions-Runtime: Änderungen nur über /api/demand/<aktion>, idempotent je
# Benutzer + Request-ID. Ab FA läuft alles im gemeinsamen Produktionskern (Bereichsvorrat, Planung, Runtime).
#   verfügbar = physisch - reserviert
#   Abrufbedarf = offen - reserviert - erwartete Menge offener Abruf-FA   (offen = Abrufmenge - geliefert)
#   Bestandsbedarf = Sollbestand - verfügbar - erwartete Menge offener Bestands-FA
# ---------------------------------------------------------------------------------------------
DEMAND_WRITE_ROLES = {"admin", "production_planning"}
DEMAND_SOURCES = {"FRAME_ORDER", "STOCK_REQUIREMENT"}
DEMAND_ACTIONS = {"frame-order-save", "call-off-create", "call-off-update", "call-off-cancel", "reserve", "fa-create", "deliver", "stock-save"}
DEMAND_LOCKED_FIELDS = ("sourceType", "sourceId", "frameOrderId", "callOffId", "stockRequirementId", "articleId", "articleNo", "targetQty")


def _demand_text(body, key, limit, required=False):
    value = body.get(key, "")
    if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
        raise ProductionError("MP-DEM-001", f"Feld '{key}' fehlt oder ist zu lang (max. {limit} Zeichen).")
    return value.strip()


def _demand_qty(body, key, positive=True):
    value = body.get(key)
    if not _nonnegative_int(value) or (positive and value <= 0) or value > 10**9:
        raise ProductionError("MP-DEM-002", f"Menge '{key}' muss eine ganze Zahl {'größer 0' if positive else 'ab 0'} sein.")
    return value


def _demand_date(body, key, required=False):
    value = body.get(key, "")
    if (value or required) and not _valid_date_key(value):
        raise ProductionError("MP-DEM-003", f"Datum '{key}' im Format JJJJ-MM-TT angeben.")
    return value or ""


def _find(items, rid, code, label):
    rec = next((x for x in items if str(x.get("id")) == str(rid)), None)
    if rec is None:
        raise ProductionError(code, f"{label} nicht gefunden.", 404)
    return rec


def _inventory_for(state, article, department, create=False):
    inv = next((x for x in state["inventory"] if str(x.get("articleId")) == article and str(x.get("departmentId")) == department), None)
    if inv is None and create:
        inv = {"id": "inv_"+secrets.token_hex(8), "articleId": article, "departmentId": department, "physicalQty": 0, "reservedQty": 0}
        state["inventory"].append(inv)
    return inv


def _expected_output(state, key, value):
    """Noch erwartete Gutmenge offener FA einer Bedarfsquelle (Teilmeldungen werden erst bei Fertigmeldung gebucht)."""
    return sum(max(0, int(x.get("targetQty") or 0) - int(x.get("scrapQty") or 0)) for x in state.get("workSteps") or [] if str(x.get(key) or "") == str(value))


def call_off_demand(state, c):
    open_qty = int(c.get("qty") or 0) - int(c.get("deliveredQty") or 0)
    return max(0, open_qty - int(c.get("reservedQty") or 0) - _expected_output(state, "callOffId", c["id"]))


def stock_demand(state, inv):
    target = int(inv.get("targetQty") or 0)
    available = int(inv.get("physicalQty") or 0) - int(inv.get("reservedQty") or 0)
    return max(0, target - available - _expected_output(state, "stockRequirementId", inv["id"]))


def _reserve_call_off(state, c):
    """Freien Bestand deterministisch für genau diesen Abruf reservieren; liefert die reservierte Menge."""
    inv = _inventory_for(state, str(c["articleId"]), str(c["departmentId"]))
    if inv is None:
        return 0
    need = int(c["qty"]) - int(c.get("deliveredQty") or 0) - int(c.get("reservedQty") or 0)
    take = max(0, min(need, int(inv.get("physicalQty") or 0) - int(inv.get("reservedQty") or 0)))
    if take:
        c["reservedQty"] = int(c.get("reservedQty") or 0) + take
        inv["reservedQty"] = int(inv.get("reservedQty") or 0) + take
    return take


def _demand_log(rec, user, stamp, action, **detail):
    rec.setdefault("log", []).append({"at": stamp, "actor": user["username"], "action": action, **detail})


def _demand_fa(state, stamp, source, source_id, department, article, description, qty, due, fa, links):
    steps = state["workSteps"]
    pos = max([_finite_float(x.get("pos")) or 0 for x in steps] + [0]) + 10
    step = {"id": "ws_"+secrets.token_hex(8), "sequence": pos, "planningType": "MACHINE", "pos": pos, "departmentId": department,
            "projectId": "", "predecessorIds": [], "fa": fa, "faNumber": fa, "order": fa, "ab": "", "wt": "",
            "machineId": "", "altMachineId": "", "allowAlternative": False, "articleNo": article, "articleId": article,
            "description": description, "targetQty": qty, "dueDate": due, "baselinePlan": None, "hours": 0,
            "goodQty": 0, "scrapQty": 0, "status": "planned", "direction": "forward", "anchorMode": "none",
            "requiredStart": "", "requiredFinish": "", "createdAt": stamp, "lockedStart": "", "lockedSegments": [],
            "actualStartedAt": "", "runningSince": "", "pausedAt": "", "pauseIntervals": [], "remainingHours": None,
            "lastStatusCheckAt": "", "handoffUnassigned": True, "sourceType": source, "sourceId": source_id, **links}
    steps.append(step)
    return step


def demand_apply(state, user, action, body, stamp=None):
    """Eine autoritative Bedarfslogik für Rahmenauftrag, Abruf, Bestand und FA-Erzeugung."""
    stamp = stamp or now_iso()
    if user["role"] not in DEMAND_WRITE_ROLES:
        raise ProductionError("MP-DEM-040", "Rahmenaufträge, Abrufe und Bestand pflegt die Arbeitsvorbereitung.", 403)
    for key in ("frameOrders", "callOffs", "inventory", "workSteps"):
        state.setdefault(key, [])
    production_departments = {str(d.get("id")) for d in state.get("departments") or [] if d.get("kind", "production") == "production" and d.get("active", True) is not False}
    result = {"action": action, "at": stamp, "actor": user["username"]}
    if action == "frame-order-save":
        department = _demand_text(body, "departmentId", 80, True)
        if department not in production_departments:
            raise ProductionError("MP-DEM-004", "Rahmenauftrag braucht einen aktiven Produktionsbereich.")
        fields = {"number": _demand_text(body, "number", 60, True), "customer": _demand_text(body, "customer", 120, True),
                  "articleId": _demand_text(body, "articleId", 80, True), "description": _demand_text(body, "description", 300),
                  "departmentId": department, "totalQty": _demand_qty(body, "totalQty"), "validTo": _demand_date(body, "validTo")}
        status = body.get("status", "open")
        if status not in {"open", "closed"}:
            raise ProductionError("MP-DEM-005", "Status muss 'open' oder 'closed' sein.")
        if any(str(f.get("number")) == fields["number"] and f.get("id") != body.get("id") for f in state["frameOrders"]):
            raise ProductionError("MP-DEM-006", f"Rahmenauftrag {fields['number']} existiert bereits.", 409)
        if body.get("id"):
            fo = _find(state["frameOrders"], body["id"], "MP-DEM-010", "Rahmenauftrag")
            calls = [c for c in state["callOffs"] if c.get("frameOrderId") == fo["id"]]
            if calls and (fields["articleId"] != fo["articleId"] or fields["departmentId"] != fo["departmentId"]):
                raise ProductionError("MP-DEM-007", "Artikel und Bereich sind nach dem ersten Abruf fest.", 409)
            called = sum(int(c.get("qty") or 0) for c in calls)
            if fields["totalQty"] < called:
                raise ProductionError("MP-DEM-008", f"Gesamtmenge kleiner als bereits abgerufen ({called}).", 409)
            changes = {k: v for k, v in fields.items() if fo.get(k) != v}
            fo.update(fields, status=status, updatedAt=stamp)
            _demand_log(fo, user, stamp, "geändert", changes=changes, status=status)
        else:
            fo = {"id": "fo_"+secrets.token_hex(8), **fields, "status": status, "createdAt": stamp, "createdBy": user["username"]}
            _demand_log(fo, user, stamp, "angelegt")
            state["frameOrders"].append(fo)
        result.update(frameOrderId=fo["id"], departmentId=fo["departmentId"])
    elif action == "call-off-create":
        fo = _find(state["frameOrders"], body.get("frameOrderId"), "MP-DEM-010", "Rahmenauftrag")
        if fo.get("status") != "open":
            raise ProductionError("MP-DEM-011", "Rahmenauftrag ist abgeschlossen.", 409)
        qty, due = _demand_qty(body, "qty"), _demand_date(body, "dueDate", True)
        remaining = int(fo["totalQty"]) - sum(int(c.get("qty") or 0) for c in state["callOffs"] if c.get("frameOrderId") == fo["id"])
        if qty > remaining:
            raise ProductionError("MP-DEM-012", f"Abrufmenge {qty} größer als Rahmenrest {remaining}.", 409)
        seq = 1 + sum(1 for c in state["callOffs"] if c.get("frameOrderId") == fo["id"])
        c = {"id": "co_"+secrets.token_hex(8), "frameOrderId": fo["id"], "number": _demand_text(body, "number", 60) or f"{fo['number']}-{seq:03d}",
             "articleId": fo["articleId"], "departmentId": fo["departmentId"], "qty": qty, "dueDate": due,
             "reservedQty": 0, "deliveredQty": 0, "status": "open", "createdAt": stamp, "createdBy": user["username"]}
        _demand_log(c, user, stamp, "angelegt", qty=qty)
        state["callOffs"].append(c)
        result.update(callOffId=c["id"], departmentId=c["departmentId"])
    elif action in {"call-off-update", "call-off-cancel"}:
        c = _find(state["callOffs"], body.get("callOffId"), "MP-DEM-020", "Abruf")
        if c.get("status") != "open":
            raise ProductionError("MP-DEM-021", "Abruf ist nicht mehr offen.", 409)
        delivered = int(c.get("deliveredQty") or 0)
        if action == "call-off-cancel":
            new_qty, due = delivered, c["dueDate"]
        else:
            new_qty, due = _demand_qty(body, "qty"), _demand_date(body, "dueDate") or c["dueDate"]
            if new_qty < delivered:
                raise ProductionError("MP-DEM-022", f"Abrufmenge kleiner als bereits geliefert ({delivered}).", 409)
            fo = _find(state["frameOrders"], c["frameOrderId"], "MP-DEM-010", "Rahmenauftrag")
            remaining = int(fo["totalQty"]) - sum(int(x.get("qty") or 0) for x in state["callOffs"] if x.get("frameOrderId") == fo["id"] and x is not c)
            if new_qty > remaining:
                raise ProductionError("MP-DEM-012", f"Abrufmenge {new_qty} größer als Rahmenrest {remaining}.", 409)
        excess = int(c.get("reservedQty") or 0) - (new_qty - delivered)
        if excess > 0:
            inv = _inventory_for(state, str(c["articleId"]), str(c["departmentId"]))
            c["reservedQty"] -= excess
            inv["reservedQty"] = int(inv.get("reservedQty") or 0) - excess
        # Noch nicht begonnene FA auf den neuen Bedarf kürzen; laufende FA produzieren in den freien Bestand.
        need = max(0, new_qty - delivered - int(c.get("reservedQty") or 0))
        planned = [x for x in state["workSteps"] if str(x.get("callOffId")) == c["id"]]
        surplus = sum(max(0, int(x.get("targetQty") or 0) - int(x.get("scrapQty") or 0)) for x in planned) - need
        for step in sorted(planned, key=lambda x: str(x.get("createdAt") or ""), reverse=True):
            if surplus <= 0:
                break
            if step.get("status") != "planned":
                continue
            cut = min(surplus, int(step.get("targetQty") or 0))
            surplus -= cut
            if cut == int(step.get("targetQty") or 0):
                if any(step["id"] in (x.get("predecessorIds") or []) for x in state["workSteps"]):
                    raise ProductionError("MP-DEM-023", f"FA {step.get('fa')} ist Vorgänger eines anderen FA und kann nicht entfallen.", 409)
                state["workSteps"].remove(step)
                result.setdefault("removedFa", []).append(step.get("fa"))
            else:
                step["targetQty"] = int(step["targetQty"]) - cut
                result.setdefault("reducedFa", []).append(step.get("fa"))
        old_qty = c["qty"]
        c.update(qty=new_qty, dueDate=due)
        if action == "call-off-cancel":
            c["status"] = "cancelled"
        elif new_qty == delivered:
            c["status"] = "delivered"
        _demand_log(c, user, stamp, "storniert" if action == "call-off-cancel" else "geändert", qty=new_qty, before=old_qty)
        result.update(callOffId=c["id"], departmentId=c["departmentId"])
    elif action == "reserve":
        c = _find(state["callOffs"], body.get("callOffId"), "MP-DEM-020", "Abruf")
        if c.get("status") != "open":
            raise ProductionError("MP-DEM-021", "Abruf ist nicht mehr offen.", 409)
        taken = _reserve_call_off(state, c)
        _demand_log(c, user, stamp, "reserviert", qty=taken)
        result.update(callOffId=c["id"], departmentId=c["departmentId"], reservedQty=taken)
    elif action == "fa-create":
        fa = _demand_text(body, "fa", 60, True)
        if body.get("callOffId"):
            c = _find(state["callOffs"], body["callOffId"], "MP-DEM-020", "Abruf")
            if c.get("status") != "open":
                raise ProductionError("MP-DEM-021", "Abruf ist nicht mehr offen.", 409)
            taken = _reserve_call_off(state, c)
            qty = call_off_demand(state, c)
            if qty <= 0:
                raise ProductionError("MP-DEM-030", "Kein offener Produktionsbedarf (Bestand reserviert bzw. FA bereits angelegt).", 409)
            fo = _find(state["frameOrders"], c["frameOrderId"], "MP-DEM-010", "Rahmenauftrag")
            step = _demand_fa(state, stamp, "FRAME_ORDER", c["id"], c["departmentId"], c["articleId"],
                              fo.get("description") or f"Abruf {c['number']} · {fo['customer']}", qty,
                              _demand_date(body, "dueDate") or c["dueDate"], fa, {"frameOrderId": fo["id"], "callOffId": c["id"]})
            _demand_log(c, user, stamp, "FA angelegt", fa=fa, qty=qty, reserved=taken)
            result.update(callOffId=c["id"], reservedQty=taken)
        elif body.get("inventoryId"):
            inv = _find(state["inventory"], body["inventoryId"], "MP-DEM-050", "Bestand")
            qty = stock_demand(state, inv)
            if qty <= 0:
                raise ProductionError("MP-DEM-030", "Kein Bestandsbedarf (Sollbestand erreicht bzw. FA bereits angelegt).", 409)
            if str(inv.get("departmentId")) not in production_departments:
                raise ProductionError("MP-DEM-004", "Bestand gehört zu keinem aktiven Produktionsbereich.")
            step = _demand_fa(state, stamp, "STOCK_REQUIREMENT", inv["id"], str(inv["departmentId"]), str(inv["articleId"]),
                              str(inv.get("description") or f"Bestand {inv['articleId']}"), qty,
                              _demand_date(body, "dueDate"), fa, {"stockRequirementId": inv["id"]})
        else:
            raise ProductionError("MP-DEM-031", "Abruf oder Bestand angeben.")
        result.update(orderId=step["id"], fa=fa, qty=step["targetQty"], departmentId=step["departmentId"])
    elif action == "deliver":
        c = _find(state["callOffs"], body.get("callOffId"), "MP-DEM-020", "Abruf")
        if c.get("status") != "open":
            raise ProductionError("MP-DEM-021", "Abruf ist nicht mehr offen.", 409)
        reserved = int(c.get("reservedQty") or 0)
        qty = body.get("qty", reserved)
        if not _nonnegative_int(qty) or qty <= 0 or qty > reserved:
            raise ProductionError("MP-DEM-032", f"Liefermenge muss zwischen 1 und der reservierten Menge ({reserved}) liegen.", 409)
        inv = _inventory_for(state, str(c["articleId"]), str(c["departmentId"]))
        inv["physicalQty"] = int(inv["physicalQty"]) - qty
        inv["reservedQty"] = int(inv["reservedQty"]) - qty
        c["reservedQty"] = reserved - qty
        c["deliveredQty"] = int(c.get("deliveredQty") or 0) + qty
        if c["deliveredQty"] >= int(c["qty"]):
            c["status"] = "delivered"
        _demand_log(c, user, stamp, "geliefert", qty=qty)
        result.update(callOffId=c["id"], departmentId=c["departmentId"], qty=qty)
    elif action == "stock-save":
        if body.get("inventoryId"):
            inv = _find(state["inventory"], body["inventoryId"], "MP-DEM-050", "Bestand")
        else:
            department = _demand_text(body, "departmentId", 80, True)
            if department not in production_departments:
                raise ProductionError("MP-DEM-004", "Bestand braucht einen aktiven Produktionsbereich.")
            inv = _inventory_for(state, _demand_text(body, "articleId", 80, True), department, create=True)
        before = {k: inv.get(k) for k in ("physicalQty", "targetQty", "description")}
        if "physicalQty" in body:
            physical = _demand_qty(body, "physicalQty", positive=False)
            if physical < int(inv.get("reservedQty") or 0):
                raise ProductionError("MP-DEM-051", f"Physischer Bestand kleiner als reserviert ({inv.get('reservedQty')}).", 409)
            inv["physicalQty"] = physical
        if "targetQty" in body:
            inv["targetQty"] = _demand_qty(body, "targetQty", positive=False)
        if "description" in body:
            inv["description"] = _demand_text(body, "description", 300)
        _demand_log(inv, user, stamp, "Bestand gepflegt", before=before)
        result.update(inventoryId=inv["id"], departmentId=inv["departmentId"])
    else:
        raise ProductionError("MP-DEM-000", "Unbekannte Bedarfsaktion.", 404)
    return result


def demand_book_completion(state, order, finished):
    """Fertig-/Abbruchmeldung eines Bedarfs-FA: Gutmenge in den Bestand, für den Abruf reservieren."""
    article = str(order.get("articleId") or order.get("articleNo") or "")
    if not article:
        raise ProductionError("MP-PROD-049", "Bestands-FA benötigt eine Artikelreferenz.")
    inv = _inventory_for(state, article, str(order.get("departmentId")), create=True)
    inv["physicalQty"] = int(inv.get("physicalQty") or 0) + int(order.get("goodQty") or 0)
    c = next((x for x in state.get("callOffs") or [] if str(x.get("id")) == str(order.get("callOffId") or "")), None)
    if c is not None and c.get("status") == "open":
        finished["reservedForCallOff"] = _reserve_call_off(state, c)
