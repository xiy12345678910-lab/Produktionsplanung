"""Füllt den Demo-Server mit erfundenen, realistischen Daten (Bereiche, Personal, Projekte, Aufträge)."""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from api import C
random.seed(4)
a=C(); assert a.login('admin','adminpass1')[0]==200
for u,r,d in [('gf','gf',''),('lead_cnc','department_lead','cnc'),('dep_cnc','department_deputy','cnc'),('lead_k1','department_lead','konf1'),('pm','project_management',''),('sales','sales',''),('av','production_planning',''),('viewer','viewer','')]:
    a.req('POST','/api/users',{'username':u,'password':'password123','role':r,'departmentId':d})
st=a.state(); d=st['data']
M={m['id']:m for m in d['machines']}
d['machines'][0]['name']='DMG DMU 65'; d['machines'][1]['name']='Hermle C42'; d['machines'][2]['name']='Mazak VCN'
names=['Anna Keller','Ben Yilmaz','Carla Schmidt','Deniz Aydin','Elif Kaya','Frank Otto','Gina Wolf','Hans Berg','Ivo Petrov','Jana Klein','Kai Neumann','Lea Fuchs','Milan Horvat','Nora Vogel']
deps=['cnc','cnc','cnc','cnc','cnc','konf1','konf1','konf1','konf2','konf2','screenprint','thermoforming','thermoforming','cnc']
emps=[]
for i,(n,dp) in enumerate(zip(names,deps)):
    ms=[m for m in M if M[m].get('departmentId')==dp]
    sk=ms[:2] if dp=='cnc' else ms
    emps.append({'id':f'e{i}','name':n,'departmentId':dp,'skills':sk,'homeMachineId':sk[0] if i%3==0 else '','homeShift':'auto','employmentType':'permanent','weeklyHours':40 if i%4 else 30,'active':True})
emps.append({'id':'et1','name':'Leih: Olga Brandt','departmentId':'konf1','skills':[],'homeShift':'auto','employmentType':'temporary','tempStatus':'requested','tempFrom':'2026-10-05','tempTo':'2026-10-23','weeklyHours':40,'active':True})
d['employees']=emps
d['personnelAbsences']=[{'employeeId':'e1','date':f'2026-10-0{x}','label':'Urlaub'} for x in (5,6,7,8,9)]+[{'employeeId':'e6','date':'2026-10-06','label':'Krank'}]
d['weeklyEmployeeDeployments']=[{'employeeId':'e13','departmentId':'konf1','weekStart':'2026-10-12'}]
d['departmentStaffNeeds']=[{'departmentId':'konf1','weekStart':'2026-10-05','requested':2},{'departmentId':'cnc','weekStart':'2026-10-12','requested':1,'confirmed':1}]
cust=['Bosch Rexroth','Festo','Krones','Trumpf','Voith','Liebherr']
projs=[]
for i in range(8):
    ph=['pm','pm','accepted','accepted','accepted','accepted','closed','lost'][i]
    p={'id':f'p{i}','number':f'P-2026-{1040+i}','phase':ph,'customer':cust[i%6],'name':['Gehäuse','Abdeckung','Halterung','Schutzhaube','Trägerplatte','Frontblende','Adapter','Deckel'][i],'contact':'','note':'','wt':f'WT-{700+i}','ab':f'AB-26-{300+i}' if ph in('accepted','closed') else '','dueDate':f'2026-{10+i//4}-{10+i*2:02d}','offer':{},'processes':[],'log':[]}
    if ph=='accepted':
        p['processes']=[{'id':f'pp{i}a','areaId':'engineering','title':'Konstruktion freigeben','status':'done','startDate':'2026-09-21','dueDate':'2026-09-25'},
                        {'id':f'pp{i}b','areaId':'purchasing','title':'Material bestellen','status':'in_progress','startDate':'2026-09-28','dueDate':'2026-10-02'},
                        {'id':f'pp{i}c','areaId':'cnc','title':'Fräsen','status':'open','startDate':'2026-10-05','dueDate':'2026-10-09'},
                        {'id':f'pp{i}d','areaId':'konf1','title':'Konfektion','status':'open','startDate':'2026-10-12','dueDate':'2026-10-16'},
                        {'id':f'pp{i}e','areaId':'screenprint','title':'Bedrucken','status':'open','startDate':'2026-10-07','dueDate':'2026-10-08'},
                        {'id':f'pp{i}f','areaId':'thermoforming','title':'Schale tiefziehen','status':'open','startDate':'2026-10-06','dueDate':'2026-10-07'}]
    projs.append(p)
d['projects']=projs
ws=[]; pos=10
for i in range(22):
    dep=random.choice(['cnc','cnc','cnc','konf1','konf2','screenprint','thermoforming'])
    ms=[m for m in M if M[m].get('departmentId')==dep]; mid=random.choice(ms)
    p=projs[2+i%4] if i%2==0 else None
    fs=f'FS-26-{5100+i}'
    o={'id':f'o{i}','projectId':p['id'] if p else '','fs':fs,'ab':p['ab'] if p else '','wt':p['wt'] if p else '','order':fs,'departmentId':dep,'planningType':'MACHINE','sequence':10*(i+1),'predecessorIds':[],'pos':pos,'machineId':mid,'altMachineId':'','allowAlternative':False,
       'articleNo':f'A-{88000+i*7}','description':random.choice(['Grundplatte Alu','Seitenteil','Deckel PC','Folie bedruckt','Schale tiefgezogen','Winkel Stahl']),'targetQty':random.choice([20,50,120,500]),'dueDate':f'2026-10-{random.randint(9,30):02d}','hours':random.choice([2,4,6.5,8,12,16,24]),
       'status':'planned','direction':'forward','anchorMode':'none','requiredStart':'','requiredFinish':'','goodQty':0,'scrapQty':0,'createdAt':'2026-09-28T08:00:00Z'}
    ws.append(o); pos+=10
d['workSteps']=ws
d['history']=[{'id':f'h{i}','originalOrderId':f'old{i}','recordType':'done','status':'done','fs':f'FS-26-{4900+i}','order':f'FS-26-{4900+i}','machineId':'m1','departmentId':'cnc','hours':6,'goodQty':50,'scrapQty':i%3,
  'actualStartedAt':f'2026-09-{14+i}T06:30:00','actualFinishedAt':f'2026-09-{14+i}T14:00:00','finishedAt':f'2026-09-{14+i}T14:00:00','actualSegments':[{'start':f'2026-09-{14+i}T06:30:00','end':f'2026-09-{14+i}T09:00:00','shift':'single'},{'start':f'2026-09-{14+i}T09:15:00','end':f'2026-09-{14+i}T12:00:00','shift':'single'},{'start':f'2026-09-{14+i}T12:30:00','end':f'2026-09-{14+i}T14:00:00','shift':'single'}],'machineName':'DMG DMU 65','description':'Grundplatte Alu','projectId':'p6' if i<2 else ''} for i in range(6)]
d['ci']={'company':'Muster *Kunststofftechnik* GmbH','address':'Industriestr. 5 · 12345 Musterstadt','footer':'','color':'#1f5eff','font':''}
d['audit'].insert(0,{'id':'aud_seed','ts':'2026-09-30T08:00:00Z','actor':'admin','action':'Testdaten','detail':'Seed','revision':1})
r=a.put(st); print('seed',r[0], r[1] if r[0]!=200 else '')
for u,d in [('lead_sd','screenprint'),('lead_tz','thermoforming'),('lead_k2','konf2')]:
    a.req('POST','/api/users',{'username':u,'password':'password123','role':'department_lead','departmentId':d})
