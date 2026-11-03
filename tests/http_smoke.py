"""Integration smoke test against a running, local server. Restores favorite state."""
import csv, io, json, re, sys, time, urllib.error, urllib.parse, urllib.request
base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8765'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));timings={}
def request(path,body=None,token=None):
    headers={}
    if body is not None:headers={'Content-Type':'application/json','X-Token':token or ''};body=json.dumps(body).encode()
    req=urllib.request.Request(base+path,data=body,headers=headers);start=time.perf_counter()
    with opener.open(req,timeout=60) as response:data=response.read();status=response.status
    timings[path.split('?')[0]]=time.perf_counter()-start
    return data,status
html,_=request('/');token=re.search(r"const token='([^']+)'",html.decode())[1]
def api(path,body=None,auth=token):return json.loads(request(path,body,auth)[0])
def search(filters,**kw):return api('/api/search?'+urllib.parse.urlencode({'filters':json.dumps(filters),**kw}))
status=api('/api/status');assert status['total']>0
filters={'prefix':'MO','length':4,'structure':'CVCV'}
rows=search(filters)['rows'];assert len(rows)==85 and all(r['name'].startswith('MO') and r['structure']=='CVCV' for r in rows)
first=search({},page_size=7);second=search({'cursor':first['next_cursor']},page_size=7)
assert len(first['rows'])==len(second['rows'])==7 and max(r['name'] for r in first['rows'])<min(r['name'] for r in second['rows'])
assert search({'exact':'MOBA'})['rows'][0]['name']=='MOBA'
assert search({'exact':"%' OR 1=1 --"})['rows']==[]
f={'q':'TOR','length':6,'suffix':'A','exclude':'ZZ','vowels_min':2};assert all('TOR' in r['name'] and r['name'].endswith('A') and r['vowels']>=2 for r in search(f)['rows'])
r=rows[0]
try:
    api('/api/favorite',{'id':r['id'],'favorite':True})
    assert any(x['id']==r['id'] for x in search({'favorites':True,'exact':r['name']})['rows'])
finally:api('/api/favorite',{'id':r['id'],'favorite':bool(r['favorite'])})
try:
    api('/api/favorite',{'id':r['id'],'favorite':True},auth='invalid')
    raise AssertionError('CSRF token not enforced')
except urllib.error.HTTPError as e:assert e.code==403
job=api('/api/export',{'filters':{'ids':[x['id'] for x in rows[:3]]},'split':2})
for _ in range(200):
    state=api('/api/job?id='+job['id'])
    if state['state'] in ['complete','error']:break
    time.sleep(.05)
assert state['state']=='complete' and state['rows']==3 and len(state['files'])==2
names=[]
for file in state['files']:
    data,_=request('/download/'+job['id']+'/'+file)
    names.extend(r['name'] for r in csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
assert set(names)=={r['name'] for r in rows[:3]}
print(json.dumps({'passed':True,'checks':['status','combined_filters','cursor_pages','exact_search','literal_sql_input','substring_suffix_counts','favorites_persist_and_restore','csrf_token','async_export','split_csv_download'],'timings_seconds':timings,'export_job':job['id']},indent=2))
