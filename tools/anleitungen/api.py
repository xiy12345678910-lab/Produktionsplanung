import json, http.client
V='12.7.5'
class C:
    def __init__(s,port=int(__import__('os').environ.get('MP_DEMO_PORT','18999'))): s.port=port; s.cookie=''
    def req(s,method,path,body=None):
        c=http.client.HTTPConnection('127.0.0.1',s.port,timeout=30)
        h={'Content-Type':'application/json','X-MP-Client-Version':V}
        if s.cookie: h['Cookie']=s.cookie
        c.request(method,path,json.dumps(body) if body is not None else None,h)
        r=c.getresponse(); sc=r.getheader('Set-Cookie')
        if sc: s.cookie=sc.split(';')[0]
        raw=r.read(); 
        try: return r.status,json.loads(raw)
        except Exception: return r.status,raw
    def login(s,u,p): return s.req('POST','/api/login',{'username':u,'password':p})
    def state(s): return s.req('GET','/api/state')[1]
    def put(s,st,action='Test'): 
        return s.req('PUT','/api/state',{'revision':st['revision'],'data':st['data'],'action':action})
