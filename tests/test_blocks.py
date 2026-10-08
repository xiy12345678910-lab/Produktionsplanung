#!/usr/bin/env python3
"""A–D acceptance: migration, capacity/scope and real HTTP retry/concurrency."""
import copy
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix='mp-blocks-'))
os.environ['MP_CONFIG_DIR'] = str(TMP / 'config')
import server
import release_gates as gates

server.DATA_DIR = TMP / 'data'
server.DB_PATH = server.DATA_DIR / 'maschinenplanung.sqlite3'
server.PBKDF2_ITERS = 1000
server.init_db(seed='werbetechnik')
server.load_config()
with server.db_session() as con:
    BASE = json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()[0])
ADMIN = {'id': 1, 'role': 'admin', 'username': 'admin', 'department_id': ''}
M = BASE['machines'][0]
DAY = '2026-10-07'
# Runtime HTTP assertions must also run outside production shifts and on weekends.
server.now_iso = lambda: DAY+'T05:00:00Z'


def state(source='PROJECT'):
    s = copy.deepcopy(BASE)
    s['workSteps'] = [{'id': 'fa_one', 'fa': '4711', 'faNumber': '4711', 'sourceType': source, 'sourceId': 'source_one', 'projectId': '', 'departmentId': M['departmentId'], 'planningType': 'MACHINE', 'sequence': 10, 'pos': 10, 'machineId': M['id'], 'altMachineId': '', 'allowAlternative': False, 'order': '4711', 'hours': 1, 'targetQty': 10000, 'status': 'released', 'direction': 'forward', 'anchorMode': 'none', 'predecessorIds': [], 'articleNo': 'ART-1', 'baselinePlan': {'machineId': M['id'], 'crew': 1, 'setupMinutes': 0}}]
    return s


class CorePlanningTests(unittest.TestCase):
    def test_legacy_migration_preserves_references_and_unknown_fields(self):
        s = {'workSteps': [{'id': 'old', 'fs': 'FS 4711', 'projectId': 'p', 'departmentId': 'cnc', 'toolId': 'tool', 'custom': {'keep': 1}}], 'history': [{'id': 'h', 'fs': '89'}], 'formats': [{'tools': [{'fs': 'FS 4711', 'stepId': 'old'}]}], 'planVersions': [{'payload': {'workSteps': [{'id': 'v', 'fs': '123'}]}}]}
        original = copy.deepcopy(s)
        server.normalize_fa_state(s)
        self.assertEqual(s['workSteps'][0]['fa'], 'FS 4711')
        self.assertEqual(s['workSteps'][0]['faNumber'], 'FS 4711')
        self.assertEqual(s['workSteps'][0]['toolId'], 'tool')
        self.assertEqual(s['formats'][0]['tools'][0]['stepId'], 'old')
        self.assertEqual(s['history'][0]['fa'], '89')
        self.assertEqual(s['planVersions'][0]['payload']['workSteps'][0]['fa'], '123')
        self.assertEqual(s['workSteps'][0]['fs'], original['workSteps'][0]['fs'])
        before = copy.deepcopy(s)
        server.normalize_fa_state(s)
        self.assertEqual(s, before)

    def test_source_types_are_canonical(self):
        for refs, expected in [({'projectId': 'p'}, 'PROJECT'), ({'frameOrderId': 'f', 'callOffId': 'c'}, 'FRAME_ORDER'), ({'stockRequirementId': 's'}, 'STOCK_REQUIREMENT')]:
            s = {'workSteps': [{'id': 'o', 'fs': '7', **refs}]}
            server.normalize_fa_state(s)
            self.assertEqual(s['workSteps'][0]['sourceType'], expected)
            self.assertTrue(s['workSteps'][0]['sourceId'])

    def test_20_hour_home_assignment(self):
        s = state()
        e = {'id': 'e', 'name': 'E', 'skills': [M['id']], 'departmentId': M['departmentId'], 'homeMachineId': M['id'], 'weeklyHours': 20, 'active': True}
        a = gates.personnel_assignment(s, e, datetime.fromisoformat(DAY), DAY)
        minutes = gates.clock_minutes(a['end']) - gates.clock_minutes(a['start'])
        minutes -= sum(gates.clock_minutes(b['end'])-gates.clock_minutes(b['start']) for b in a['breaks'])
        self.assertEqual(minutes, 240)
        e.update(workingDays=[1,3], dailyHours={'1': 6, '3': 4})
        self.assertEqual(gates.daily_hours(e, datetime.fromisoformat(DAY)), 4)
        self.assertEqual(gates.daily_hours(e, datetime(2026,10,8)), 0)

    def test_deployment_does_not_grant_qualification(self):
        s = state()
        s['weeklyEmployeeDeployments'] = [{'employeeId': 'e', 'weekStart': '2026-10-05', 'departmentId': M['departmentId']}]
        e = {'id': 'e', 'departmentId': 'other', 'skills': []}
        self.assertFalse(gates.can_staff(s, e, M['id'], datetime.fromisoformat(DAY)))
        e['skills'] = [M['id']]
        self.assertTrue(gates.can_staff(s, e, M['id'], datetime.fromisoformat(DAY)))
        self.assertFalse(gates.can_staff(s, e, M['id'], datetime(2026,10,12)))

    def test_absence_removes_capacity(self):
        s = state()
        e = {'id':'e','departmentId':M['departmentId'],'skills':[M['id']],'active':True}
        s['personnelAbsences'] = [{'employeeId':'e','date':DAY,'label':'Urlaub'}]
        self.assertTrue(gates.is_absent(s,e,DAY))

    def test_read_scope_and_roundtrip_preserve_foreign_records(self):
        s = state()
        s['workSteps'].append({**s['workSteps'][0], 'id':'foreign', 'departmentId':'packing', 'fa':'SECRET'})
        user = {'role':'department_lead','department_id':M['departmentId'],'username':'lead'}
        shown = server.redact_state(s,user)
        self.assertEqual([x['id'] for x in shown['workSteps']], ['fa_one'])
        self.assertNotIn('SECRET',json.dumps(shown))
        server.unredact_incoming(s, shown, user)
        self.assertEqual(sorted(x['id'] for x in shown['workSteps']), ['fa_one','foreign'])
        self.assertTrue(server.department_change_allowed(s, shown, M['departmentId'])[0])

    def test_read_scope_default_deny(self):
        shown = server.redact_state(state(), {'role':'production','department_id':'','username':'prod'})
        self.assertEqual(shown['workSteps'], [])

    def test_av_cannot_operatively_replan(self):
        a = state(); a['workSteps'][0]['status'] = 'planned'
        b = copy.deepcopy(a); b['workSteps'][0]['laneIndex'] = 2
        self.assertFalse(server.production_planning_change_allowed(a,b)[0])

    def test_department_cannot_change_av_hours(self):
        a = state(); a['workSteps'][0].update(planningType='LABOR_HOURS',requiredHours=40)
        b = copy.deepcopy(a); b['workSteps'][0]['requiredHours'] = 20
        self.assertFalse(server.department_change_allowed(a,b,M['departmentId'])[0])

    def test_runtime_all_sources_quantities_time_and_feedback(self):
        for source in server.FA_SOURCE_TYPES:
            s = state(source)
            server.production_apply(s, ADMIN, 'fa_one','start',{'requestId':'start-1234'},'2026-10-07T05:00:00Z')
            server.production_apply(s, ADMIN, 'fa_one','pause',{'requestId':'pause-1234'},'2026-10-07T06:00:00Z')
            server.production_apply(s, ADMIN, 'fa_one','resume',{'requestId':'resume-1234'},'2026-10-07T06:30:00Z')
            server.production_apply(s, ADMIN, 'fa_one','partial',{'requestId':'partial-1234','goodQty':3000},'2026-10-07T06:45:00Z')
            server.production_apply(s, ADMIN, 'fa_one','partial',{'requestId':'partial-2345','goodQty':2000,'scrapQty':100},'2026-10-07T06:50:00Z')
            self.assertEqual(s['workSteps'][0]['remainingQty'],4900)
            server.production_apply(s, ADMIN, 'fa_one','finish',{'requestId':'finish-1234','goodQty':4900},'2026-10-07T07:00:00Z')
            h = s['history'][0]
            self.assertEqual(h['goodQty'],9900)
            self.assertEqual(h['scrapQty'],100)
            self.assertAlmostEqual(h['actualWorkHours'],1.5)
            self.assertAlmostEqual(h['actualPersonHours'],1.5)
            self.assertEqual(h['sourceType'],source)
            self.assertEqual(len(h['partialCompletions']),3)
            self.assertFalse(s['workSteps'])
            if source!='PROJECT':self.assertEqual(s['inventory'][0]['physicalQty'],9900)

    def test_runtime_rejects_negative_overproduction_and_bad_status(self):
        s = state()
        with self.assertRaises(server.ProductionError):server.production_apply(s,ADMIN,'fa_one','pause',{'requestId':'pause-1234'})
        server.production_apply(s,ADMIN,'fa_one','start',{'requestId':'start-1234'})
        for qty in [-1,10001,True]:
            with self.assertRaises(server.ProductionError):server.production_apply(s,ADMIN,'fa_one','partial',{'requestId':'partial-1234','goodQty':qty})

    def test_production_cannot_cross_department(self):
        with self.assertRaises(server.ProductionError) as ctx:
            server.production_apply(state(), {'role':'production','department_id':'foreign','username':'prod'},'fa_one','start',{'requestId':'start-1234'})
        self.assertEqual(ctx.exception.status,403)

    def test_label_real_data_sequence_and_best_before(self):
        s = state();s['palletTemplates']=[{'id':'tpl','shelfLifeDays':30,'fromAddress':'Firma A','toAddress':'Kunde B'}]
        for i in range(2):server.production_apply(s,ADMIN,'fa_one','label',{'requestId':f'label-000{i}','quantity':100,'templateId':'tpl'},'2026-10-07T05:00:00Z')
        self.assertEqual([x['sequence'] for x in s['palletLabels']],[1,2])
        self.assertEqual(s['palletLabels'][0]['bestBefore'],'2026-11-06')
        self.assertEqual(s['palletLabels'][0]['fromAddress'],'Firma A')
        self.assertNotEqual(s['palletLabels'][0]['barcode'],s['palletLabels'][1]['barcode'])

    def test_crew_maximum_and_effort_use_actual_staff_per_slot(self):
        for count,wall in [(2,20),(4,10)]:
            s=state();m=next(x for x in s['machines'] if x['id']==M['id'])
            m.update(kind='line',crew=4,crewMax=4,effortScaling=True,staffRequired=1)
            s['personnelGate']=True;s['shiftTemplates']['single']={'start':'06:00','end':'14:00','breaks':[]}
            s['employees']=[{'id':f'e{i}','name':f'E{i}','departmentId':M['departmentId'],'skills':[M['id']],'homeMachineId':M['id'],'homeShift':'auto','weeklyHours':40,'active':True} for i in range(count)]
            s['workSteps'][0].update(hours=40,laneIndex=1);s['workSteps'][0]['baselinePlan']['effort']=True
            server.production_apply(s,ADMIN,'fa_one','start',{'requestId':'crew-start-1234'},'2026-10-07T04:00:00Z')
            o=s['workSteps'][0];self.assertEqual(o['productionPhases'][0]['crew'],count)
            self.assertAlmostEqual(o['remainingHours'],wall)
            server.production_apply(s,ADMIN,'fa_one','pause',{'requestId':'crew-pause-1234'},'2026-10-07T05:00:00Z')
            self.assertAlmostEqual(o['remainingHours'],wall-1)

    def test_part_time_actual_hours_use_frozen_staffing(self):
        s=state();s['personnelGate']=True;s['shiftTemplates']['single']={'start':'06:00','end':'14:00','breaks':[]}
        s['employees']=[{'id':'pt','name':'Teilzeit','departmentId':M['departmentId'],'skills':[M['id']],'homeMachineId':M['id'],'weeklyHours':20,'active':True}]
        s['workSteps'][0]['hours']=40;s['workSteps'][0]['baselinePlan']['effort']=True
        server.production_apply(s,ADMIN,'fa_one','start',{'requestId':'parttime-start-1234'},'2026-10-07T04:00:00Z')
        s['employees'][0]['weeklyHours']=40  # later planning changes cannot rewrite the running phase
        server.production_apply(s,ADMIN,'fa_one','finish',{'requestId':'parttime-finish-1234'},'2026-10-07T10:00:00Z')
        h=s['history'][0];self.assertAlmostEqual(h['actualMachineHours'],6);self.assertAlmostEqual(h['actualPersonHours'],4)
        self.assertEqual(h['productionPhases'][0]['capacity']['employees'][0]['weeklyHours'],20)

    def test_separate_slots_keep_distinct_employee_snapshots(self):
        s=state();m=next(x for x in s['machines'] if x['id']==M['id']);m.update(lanes=2,staffRequired=1)
        s['personnelGate']=True;s['employees']=[{'id':f'e{i}','name':f'E{i}','departmentId':M['departmentId'],'skills':[M['id']],'homeMachineId':M['id'],'homeLaneIndex':i,'weeklyHours':40,'active':True} for i in [1,2]]
        s['workSteps'][0]['laneIndex']=1;s['workSteps'].append({**copy.deepcopy(s['workSteps'][0]),'id':'fa_two','laneIndex':2})
        for oid in ['fa_one','fa_two']:server.production_apply(s,ADMIN,oid,'start',{'requestId':oid+'-slot-start'},'2026-10-07T05:00:00Z')
        self.assertEqual([o['productionPhases'][0]['employees'][0]['id'] for o in s['workSteps']],['e1','e2'])
        s['workSteps'].append({**copy.deepcopy(state()['workSteps'][0]),'id':'fa_three','laneIndex':1})
        with self.assertRaises(server.ProductionError):server.production_apply(s,ADMIN,'fa_three','start',{'requestId':'third-slot-start'},'2026-10-07T05:00:00Z')

    def test_foreign_dependency_exposes_status_only(self):
        s=state();s['workSteps'][0]['predecessorIds']=['foreign']
        s['workSteps'].append({**copy.deepcopy(s['workSteps'][0]),'id':'foreign','departmentId':'other','fa':'SECRET','predecessorIds':[],'status':'planned'})
        shown=server.redact_state(s,{'username':'lead','role':'department_lead','department_id':M['departmentId']})
        self.assertEqual(shown['dependencyStatus'],{'foreign':'planned'})
        self.assertNotIn('SECRET',json.dumps(shown))
        with self.assertRaises(server.ProductionError):server.production_apply(s,ADMIN,'fa_one','start',{'requestId':'blocked-start-1234'},'2026-10-07T05:00:00Z')


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.ALLOWED_NETWORK=server.ipaddress.ip_network('127.0.0.0/8')
        server.create_or_reset_admin('admin','Test-Passwort-1')
        cls.httpd=server.MPHTTPServer(('127.0.0.1',0),server.Handler)
        server.Handler.log_message=lambda *a,**k:None
        cls.port=cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever,daemon=True).start()
        _,_,cls.cookie=cls.req('POST','/api/login',{'username':'admin','password':'Test-Passwort-1'},cookie='')

    @classmethod
    def tearDownClass(cls):cls.httpd.shutdown();cls.httpd.server_close()

    @classmethod
    def req(cls,method,path,body=None,cookie=None):
        conn=http.client.HTTPConnection('127.0.0.1',cls.port,timeout=10)
        headers={'Content-Type':'application/json','X-MP-Client-Version':server.APP_VERSION}
        headers['Cookie']=cookie if cookie is not None else cls.cookie
        conn.request(method,path,json.dumps(body) if body is not None else None,headers)
        r=conn.getresponse();status=r.status;ck=(r.getheader('Set-Cookie') or '').split(';')[0];payload=json.loads(r.read() or '{}');conn.close();return status,payload,ck

    def setUp(self):
        with server.db_session() as con:
            con.execute('DELETE FROM production_requests')
            con.execute('UPDATE state SET json=?,revision=10 WHERE id=1',(json.dumps(state('STOCK_REQUIREMENT')),))

    def test_concurrent_retry_books_exactly_once(self):
        path='/api/production/fa_one/start';body={'requestId':'retry-start-1234'}
        with ThreadPoolExecutor(max_workers=2) as ex:responses=list(ex.map(lambda _:self.req('POST',path,body),range(2)))
        self.assertEqual([r[0] for r in responses],[200,200])
        self.assertEqual(sum(r[1]['replayed'] for r in responses),1)
        path='/api/production/fa_one/partial';body={'requestId':'retry-partial-1234','goodQty':3000}
        self.assertEqual(self.req('POST',path,body)[0],200)
        self.assertEqual(self.req('POST',path,body)[1]['data']['workSteps'][0]['goodQty'],3000)
        changed={**body,'goodQty':2000}
        self.assertEqual(self.req('POST',path,changed)[0],409)
        path='/api/production/fa_one/finish';body={'requestId':'retry-finish-1234','goodQty':7000}
        self.assertEqual(self.req('POST',path,body)[0],200)
        self.assertEqual(self.req('POST',path,body)[1]['data']['inventory'][0]['physicalQty'],10000)
        # A receipt remains valid when the completed FA moves out of live history.
        with server.db_session() as con:
            s=json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()[0]);s['history']=[]
            con.execute('UPDATE state SET json=? WHERE id=1',(json.dumps(s),))
        replay=self.req('POST',path,body)
        self.assertEqual(replay[0],200)
        self.assertTrue(replay[1]['replayed'])
        self.assertEqual(replay[1]['data']['inventory'][0]['physicalQty'],10000)

    def test_global_roles_clear_stale_area_and_delete_invalidates_session(self):
        status,created,_=self.req('POST','/api/users',{'username':'remove-me','password':'Test-Passwort-1','role':'production_planning','departmentId':M['departmentId']})
        self.assertEqual(status,201)
        self.assertEqual(created['departmentId'],'')
        _,_,ck=self.req('POST','/api/login',{'username':'remove-me','password':'Test-Passwort-1'},cookie='')
        self.assertEqual(self.req('DELETE','/api/users/'+str(created['id']))[0],200)
        self.assertEqual(self.req('GET','/api/state',cookie=ck)[0],401)
        self.assertEqual(self.req('POST','/api/users',{'username':'remove-me','password':'Test-Passwort-1','role':'viewer'})[0],409)
        self.assertEqual(self.req('DELETE','/api/users/1')[0],400)
        with server.db_session() as con:self.assertTrue(con.execute("SELECT 1 FROM server_audit WHERE action='Benutzer gelöscht'").fetchone())

    def test_direct_state_cannot_forge_runtime(self):
        _,body,_=self.req('GET','/api/state')
        body['data']['workSteps'][0]['goodQty']=42
        self.assertEqual(self.req('PUT','/api/state',body)[0],403)

    def test_scoped_role_requires_existing_department(self):
        status,_,_=self.req('POST','/api/users',{'username':'bad-area','password':'Test-Passwort-1','role':'department_lead','departmentId':'does-not-exist'})
        self.assertEqual(status,400)

    def test_update_api_is_admin_only_and_idempotent(self):
        from app_updates import UpdateManager
        from test_updates import source
        m=UpdateManager(TMP, '12.19.0', lambda:{'update':{'source':'owner/repo'}},lambda job:None,source()[0])
        m.check();server.UPDATE_MANAGER=m
        (m.folder/'update.lock').unlink(missing_ok=True)
        status,created,_=self.req('POST','/api/users',{'username':'update-viewer','password':'Test-Passwort-1','role':'viewer'})
        self.assertEqual(status,201)
        _,_,ck=self.req('POST','/api/login',{'username':'update-viewer','password':'Test-Passwort-1'},cookie='')
        self.assertEqual(self.req('GET','/api/updates',cookie=ck)[0],403)
        self.assertEqual(self.req('POST','/api/updates/install',{},cookie=ck)[0],403)
        self.assertEqual(self.req('POST','/api/updates/install',{'path':'anything'})[0],400)
        first=self.req('POST','/api/updates/install',{})
        second=self.req('POST','/api/updates/install',{})
        self.assertEqual(first[0],202);self.assertEqual(second[0],200)
        self.assertEqual(first[1]['job']['jobId'],second[1]['job']['jobId'])
        self.assertTrue(second[1]['alreadyRunning'])
        (m.folder/'update.lock').unlink(missing_ok=True)
        server.UPDATE_MANAGER=None

    def test_audit_contains_authoritative_field_diffs(self):
        _,response,_=self.req('GET','/api/state')
        response['data']['workSteps'][0].update(status='planned',baselinePlan=None)
        status,response,_=self.req('PUT','/api/state',response)
        self.assertEqual(status,200,response)
        _,response,_=self.req('GET','/api/state')
        response['data']['workSteps'][0]['description']='Changed'
        status,body,_=self.req('PUT','/api/state',response)
        self.assertEqual(status,200,body)
        record=next(x for x in body['data']['audit'] if x.get('field')=='description')
        self.assertEqual(record['new'],'Changed')
        self.assertEqual(record['role'],'admin')
        self.assertEqual(record['departmentId'],M['departmentId'])
        self.assertEqual(record['fa'],'4711')

    def test_scoped_save_with_interleaved_foreign_bookings(self):
        # Bereichsrollen sehen nur eigene Buchungen; der Server ergänzt fremde am Listenende.
        other=next(d['id'] for d in BASE['departments'] if d['id']!=M['departmentId'])
        s=state();s['workSteps']=[]
        s['productionEvents']=[{'id':f'e{i}','departmentId':dep,'orderId':f'o{i}'} for i,dep in enumerate([M['departmentId'],other,M['departmentId']])]
        with server.db_session() as con:con.execute('UPDATE state SET json=?,revision=10 WHERE id=1',(json.dumps(s),))
        self.assertEqual(self.req('POST','/api/users',{'username':'lead-interleave','password':'Test-Passwort-1','role':'department_lead','departmentId':M['departmentId']})[0],201)
        _,_,ck=self.req('POST','/api/login',{'username':'lead-interleave','password':'Test-Passwort-1'},cookie='')
        _,body,_=self.req('GET','/api/state',cookie=ck)
        self.assertEqual([e['id'] for e in body['data']['productionEvents']],['e0','e2'])
        body['data']['ui']={**body['data'].get('ui',{}),'week':'2026-10-12'}
        status,response,_=self.req('PUT','/api/state',body,cookie=ck)
        self.assertEqual(status,200,response)
        with server.db_session() as con:saved=json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()[0])
        self.assertEqual([e['id'] for e in saved['productionEvents']],['e0','e1','e2'])
        body['data']['productionEvents'][0]['orderId']='forged'
        body['revision']=response['revision']
        self.assertEqual(self.req('PUT','/api/state',body,cookie=ck)[0],403)

    def test_forged_source_and_fa_reference_are_rejected(self):
        for change in [{'sourceType':'UNKNOWN'},{'faNumber':'different'}]:
            _,body,_=self.req('GET','/api/state');body['data']['workSteps'][0].update(change)
            self.assertEqual(self.req('PUT','/api/state',body)[0],400)


if __name__=='__main__':unittest.main(verbosity=2)
