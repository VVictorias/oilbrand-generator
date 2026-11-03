"""Loopback-only local browser UI. No dependencies or external requests."""
import base64, concurrent.futures, json, pathlib, secrets, socketserver, sqlite3, threading, time, urllib.parse, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from brandgen import ROOT, connect, export_rows
SORTS={'name','length','vowels','consonants','structure'}
def query_filters(f, pagination=False):
    clauses=[];p=[]
    def add(sql,*v):clauses.append(sql);p.extend(v)
    def integer(key,low=0,high=6):
        value=f.get(key)
        if value is None or value=='':return None
        n=int(value)
        if not low<=n<=high:raise ValueError('Invalid '+key)
        return n
    def text(key):return str(f.get(key,'')).strip().upper()
    if text('exact'):add('c.name=?',text('exact'))
    if text('q'):add('instr(c.name,?)>0',text('q'))
    for key in ['length','vowels','consonants','syllables']:
        n=integer(key)
        if n is not None:add('c.'+key+'=?',n)
    for key,col in [('vowels_min','vowels'),('consonants_min','consonants')]:
        n=integer(key)
        if n is not None:add('c.'+col+'>=?',n)
    for key,col in [('vowels_max','vowels'),('consonants_max','consonants')]:
        n=integer(key)
        if n is not None:add('c.'+col+'<=?',n)
    for key,col in [('prefix','name'),('suffix','reversed')]:
        value=text(key)
        if value:
            if any(not 'A'<=x<='Z' for x in value):raise ValueError('Prefix/suffix must use A–Z')
            if key=='suffix':value=value[::-1]
            add(f'c.{col}>=? AND c.{col}<?',value,value+'[')
    if text('structure'):
        s=text('structure')
        if any(x not in 'CV' for x in s):raise ValueError('Structure must use C/V')
        add('c.structure=?',s)
    for s in text('exclude').split(','):
        if s.strip():add('instr(c.name,?)=0',s.strip())
    for s in text('exclude_suffix').split(','):
        if s.strip():add('substr(c.name,-?)!=?',len(s.strip()),s.strip())
    if f.get('favorites'):add('f.candidate_id IS NOT NULL')
    if f.get('no_repeats'):add("c.repeated_letters=''")
    if f.get('warning_only'):add("c.warning_suffixes!=''")
    if text('phonetics'):add('instr(c.phonetics,?)>0',str(f['phonetics']).lower())
    if 'ids' in f:
        ids=[int(x) for x in f['ids']]
        if not ids:add('0')
        else:
            # JSON table avoids SQLite's variable-count limit for a large selection.
            add('c.id IN (SELECT value FROM json_each(?))',json.dumps(ids))
    sort=str(f.get('sort','name'));direction=str(f.get('direction','asc')).lower()
    if sort not in SORTS or direction not in ('asc','desc'):raise ValueError('Invalid sort')
    if pagination and f.get('cursor'):
        try:
            cur=json.loads(base64.urlsafe_b64decode(f['cursor']).decode())
            if len(cur)!=2:raise ValueError()
        except Exception:raise ValueError('Invalid cursor')
        op='>' if direction=='asc' else '<'
        if sort=='name':add(f'c.name {op} ?',cur[1])
        else:add(f'(c.{sort},c.name) {op} (?,?)',cur[0],cur[1])
    order=f'c.{sort} {direction}, c.name {direction}' if sort!='name' else f'c.name {direction}'
    return ('WHERE '+ ' AND '.join('('+s+')' for s in clauses) if clauses else ''),p,order

def serve(database,port):
    database=str(pathlib.Path(database).resolve())
    if not pathlib.Path(database).is_file():raise ValueError('Generate database first')
    token=secrets.token_urlsafe(32);jobs={};lock=threading.Lock();executor=concurrent.futures.ThreadPoolExecutor(max_workers=1)
    def export_job(job_id,filters,split):
        db=None
        try:
            with lock:jobs[job_id]['state']='running'
            db=connect(database);where,params,order=query_filters(filters)
            result=export_rows(db,where,params,ROOT/'exports'/job_id,split,order)
            with lock:jobs[job_id].update(state='complete',rows=result['rows'],files=[pathlib.Path(x).name for x in result['files']])
        except Exception as e:
            with lock:jobs[job_id].update(state='error',error=str(e))
        finally:
            if db:db.close()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,format,*args):pass
        def response(self,status,obj):
            data=json.dumps(obj,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
        def trusted(self):
            host=self.headers.get('Host','').split(':')[0]
            return host in ('localhost','127.0.0.1')
        def do_GET(self):
            if not self.trusted():self.response(403,{'error':'Loopback host required'});return
            db=None
            try:
                url=urllib.parse.urlparse(self.path);query=urllib.parse.parse_qs(url.query)
                if url.path=='/':
                    data=(ROOT/'static/index.html').read_text().replace('__TOKEN__',token).encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'");self.end_headers();self.wfile.write(data);return
                if url.path.startswith('/download/'):
                    parts=url.path.split('/')
                    if len(parts)!=4 or not all(x and x==pathlib.Path(x).name for x in parts[2:]):raise ValueError('Invalid path')
                    job,file=parts[2:]
                    with lock:valid=job in jobs and file in jobs[job].get('files',[])
                    if not valid:raise ValueError('Unknown export')
                    path=ROOT/'exports'/job/file;self.send_response(200);self.send_header('Content-Type','text/csv; charset=utf-8');self.send_header('Content-Length',str(path.stat().st_size));self.send_header('Content-Disposition',f'attachment; filename="{file}"');self.end_headers()
                    with path.open('rb') as stream:
                        while data:=stream.read(1024*1024):self.wfile.write(data)
                    return
                if url.path=='/api/job':
                    with lock:result=dict(jobs.get(query.get('id',[''])[0],{'state':'unknown'}))
                    self.response(200,result);return
                db=connect(database)
                if url.path=='/api/status':
                    meta=dict(db.execute('SELECT key,value FROM metadata'));rows=db.execute('SELECT length,sum(accepted) FROM checkpoints GROUP BY length').fetchall()
                    self.response(200,{'status':meta['status'],'counts':dict(rows),'total':sum(r[1] for r in rows),'favorites':db.execute('SELECT count(*) FROM favorites').fetchone()[0],'snapshot':json.loads(meta['snapshot'])});return
                if url.path in ('/api/search','/api/count'):
                    f=json.loads(query.get('filters',['{}'])[0]);where,params,order=query_filters(f,pagination=url.path=='/api/search')
                    base=' FROM candidates c LEFT JOIN favorites f ON f.candidate_id=c.id '
                    if url.path=='/api/count':self.response(200,{'count':db.execute('SELECT count(*)'+base+where,params).fetchone()[0]});return
                    limit=int(query.get('page_size',['100'])[0])
                    if not 1<=limit<=200:raise ValueError('page_size: 1..200')
                    rows=[dict(r) for r in db.execute('SELECT c.*, (f.candidate_id IS NOT NULL) AS favorite'+base+where+' ORDER BY '+order+' LIMIT ?',params+[limit+1])]
                    more=len(rows)>limit;rows=rows[:limit];cursor=None
                    if more and rows:
                        r=rows[-1];cursor=base64.urlsafe_b64encode(json.dumps([r[f.get('sort','name')],r['name']]).encode()).decode()
                    self.response(200,{'rows':rows,'next_cursor':cursor});return
                self.response(404,{'error':'Not found'})
            except (ValueError,TypeError,sqlite3.Error) as e:self.response(400,{'error':str(e)})
            except (BrokenPipeError,ConnectionResetError):pass
            finally:
                if db:db.close()
        def do_POST(self):
            if not self.trusted() or self.headers.get('X-Token')!=token:self.response(403,{'error':'Invalid local token'});return
            db=None
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=8*1024*1024:raise ValueError('Invalid request size')
                body=json.loads(self.rfile.read(size));path=urllib.parse.urlparse(self.path).path
                if path=='/api/favorite':
                    db=connect(database);candidate=int(body['id'])
                    if body['favorite']:db.execute('INSERT OR IGNORE INTO favorites(candidate_id) VALUES(?)',(candidate,))
                    else:db.execute('DELETE FROM favorites WHERE candidate_id=?',(candidate,))
                    db.commit();self.response(200,{'ok':True});return
                if path=='/api/export':
                    filters=body.get('filters',{});query_filters(filters);split=int(body.get('split',1000000))
                    if split<1:raise ValueError('split must be positive')
                    job=uuid.uuid4().hex
                    with lock:jobs[job]={'id':job,'state':'queued'}
                    executor.submit(export_job,job,filters,split);self.response(202,{'id':job});return
                self.response(404,{'error':'Not found'})
            except (ValueError,KeyError,TypeError,sqlite3.Error) as e:self.response(400,{'error':str(e)})
            finally:
                if db:db.close()
    class LocalServer(ThreadingHTTPServer):
        def server_bind(self):
            # Avoid reverse DNS for a strictly numeric loopback listener.
            socketserver.TCPServer.server_bind(self)
            self.server_name='localhost';self.server_port=self.server_address[1]
    http=LocalServer(('127.0.0.1',port),Handler);http.daemon_threads=True
    print(f'Open http://127.0.0.1:{port} — Ctrl+C to stop',flush=True)
    try:http.serve_forever()
    except KeyboardInterrupt:pass
    finally:http.server_close();executor.shutdown(wait=True)
