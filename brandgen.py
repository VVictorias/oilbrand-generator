#!/usr/bin/env python3
"""Deterministic exhaustive brand generator; Python standard library only."""
import argparse, csv, hashlib, json, os, pathlib, shutil, sqlite3, subprocess, sys, time
ROOT = pathlib.Path(__file__).resolve().parent
SCHEMA = '''
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE rules(kind TEXT NOT NULL,ordinal INTEGER NOT NULL,value TEXT NOT NULL,PRIMARY KEY(kind,ordinal));
CREATE TABLE candidates(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,length INTEGER NOT NULL,first TEXT NOT NULL,last TEXT NOT NULL,reversed TEXT NOT NULL,vowels INTEGER NOT NULL,consonants INTEGER NOT NULL,structure TEXT NOT NULL,transcription TEXT NOT NULL,syllables INTEGER NOT NULL,repeated_letters TEXT NOT NULL,repeated_pairs TEXT NOT NULL,warning_suffixes TEXT NOT NULL,phonetics TEXT NOT NULL,ambiguous INTEGER NOT NULL);
CREATE TABLE checkpoints(shard TEXT PRIMARY KEY,length INTEGER,raw INTEGER,accepted INTEGER,forbidden_letter INTEGER,consonant_run INTEGER,vowel_run INTEGER,identical_run INTEGER,forbidden_substring INTEGER,forbidden_suffix INTEGER,stop_match INTEGER);
CREATE TABLE favorites(candidate_id INTEGER PRIMARY KEY REFERENCES candidates(id),created_at TEXT DEFAULT CURRENT_TIMESTAMP);
'''
INDEXES = [
'CREATE INDEX IF NOT EXISTS idx_length_name ON candidates(length,name)',
'CREATE INDEX IF NOT EXISTS idx_first_name ON candidates(first,name)',
'CREATE INDEX IF NOT EXISTS idx_last_name ON candidates(last,name)',
'CREATE INDEX IF NOT EXISTS idx_reverse ON candidates(reversed)',
'CREATE INDEX IF NOT EXISTS idx_structure_name ON candidates(structure,name)',
'CREATE INDEX IF NOT EXISTS idx_vowels_name ON candidates(vowels,name)',
'CREATE INDEX IF NOT EXISTS idx_consonants_name ON candidates(consonants,name)']
def canonical(obj): return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',',':'))
def load_config(path):
    path=pathlib.Path(path).resolve(); c=json.loads(path.read_text())
    if c['alphabet']!='ABCDEFGHIJKLMNOPQRSTUVWXYZ': raise ValueError('Alphabet must be A–Z')
    if not c['lengths'] or len(set(c['lengths']))!=len(c['lengths']) or any(type(n)!=int or not 1<=n<=6 for n in c['lengths']): raise ValueError('lengths: unique integers 1..6')
    def letters(s): return isinstance(s,str) and bool(s) and all('A'<=x<='Z' for x in s)
    if not letters(c['vowels']) or len(set(c['vowels']))!=len(c['vowels']): raise ValueError('Invalid vowels')
    for key in ('forbidden_letters','forbidden_substrings','forbidden_suffixes'):
        if not isinstance(c[key],list) or any(not letters(x) or (key=='forbidden_letters' and len(x)!=1) for x in c[key]): raise ValueError('Invalid '+key)
    for key in ('max_consecutive_consonants','max_consecutive_vowels','max_identical_run'):
        if type(c[key])!=int or not 0<=c[key]<=6: raise ValueError('Invalid '+key)
    a=c['analysis']
    if not isinstance(a['ambiguous_letters'],str) or any(not 'A'<=x<='Z' for x in a['ambiguous_letters']): raise ValueError('Invalid ambiguous_letters')
    if any(not letters(x) for x in a['warning_suffixes']): raise ValueError('Invalid warning suffix')
    if set(a['transcription'])!=set(c['alphabet']) or any(not isinstance(v,str) or not v for v in a['transcription'].values()): raise ValueError('Transcription must map all 26 letters')
    dictionaries=[]; stop=set()
    for filename in c['stop_dictionary_files']:
        p=(path.parent/filename).resolve(); data=p.read_bytes()
        words=[]
        for line in data.decode('utf-8-sig').splitlines():
            w=line.strip().upper()
            if not w or w.startswith('#'): continue
            if not letters(w): raise ValueError(f'Invalid dictionary entry: {p}: {w}')
            words.append(w)
        stop.update(words); dictionaries.append({'path':str(p),'sha256':hashlib.sha256(data).hexdigest(),'entries':len(words)})
    return c,sorted(stop),dictionaries

def connect(path):
    db=sqlite3.connect(path,timeout=30); db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON'); return db

def initialize(path,config):
    c,stop,dictionaries=load_config(config)
    snapshot={'rules':c,'stop_words':stop,'dictionaries':dictionaries,'engine_version':1}
    fingerprint=hashlib.sha256(canonical(snapshot).encode()).hexdigest()
    path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    db=connect(path)
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='metadata'").fetchone():
        old=db.execute("SELECT value FROM metadata WHERE key='fingerprint'").fetchone()[0]
        if old!=fingerprint:
            db.close()
            raise ValueError('Configuration changed. Use another database; existing results were preserved.')
    else:
        db.executescript(SCHEMA)
        db.executemany('INSERT INTO metadata VALUES(?,?)',[('snapshot',canonical(snapshot)),('fingerprint',fingerprint),('status','initialized')])
        rules={k:[str(c[k])] for k in ('alphabet','vowels','max_consecutive_consonants','max_consecutive_vowels','max_identical_run')}
        rules.update({k:c[k] for k in ('lengths','forbidden_substrings','forbidden_suffixes')})
        rules['forbidden_letters']=[''.join(c['forbidden_letters'])];rules['stop']=stop
        rules['warning_suffixes']=c['analysis']['warning_suffixes'];rules['ambiguous_letters']=[c['analysis']['ambiguous_letters']]
        rules.update({'tr_'+k:[v] for k,v in c['analysis']['transcription'].items()})
        db.executemany('INSERT INTO rules VALUES(?,?,?)',[(k,i,str(v)) for k,vs in rules.items() for i,v in enumerate(vs)])
        db.commit()
    db.close();return snapshot

def build():
    binary=ROOT/'work/generator';binary.parent.mkdir(exist_ok=True)
    source=ROOT/'src/generator.cpp'
    if not binary.exists() or binary.stat().st_mtime<source.stat().st_mtime:
        subprocess.run([os.environ.get('CXX','clang++'),'-O3','-std=c++17',str(source),'-lsqlite3','-o',str(binary)],check=True)
    return binary

def generate(args):
    snapshot=initialize(args.db,args.config); binary=build(); db=connect(args.db)
    start=time.time();db.execute("UPDATE metadata SET value='generating' WHERE key='status'");db.commit();db.close()
    try: subprocess.run([str(binary),str(pathlib.Path(args.db).resolve())],check=True)
    except (KeyboardInterrupt,subprocess.CalledProcessError):
        print('Interrupted: committed shards preserved. Run the same command to resume.',file=sys.stderr);return 1
    db=connect(args.db);db.execute("UPDATE metadata SET value='indexing' WHERE key='status'");db.commit()
    for sql in INDEXES:
        print(sql,flush=True);db.execute(sql);db.commit()
    db.execute('ANALYZE');db.execute("UPDATE metadata SET value='complete' WHERE key='status'")
    db.execute("INSERT OR REPLACE INTO metadata VALUES('last_invocation_seconds',?)",(str(time.time()-start),));db.commit()
    db.execute('PRAGMA wal_checkpoint(TRUNCATE)');db.close()
    return 0

def expected_counts(c,stop):
    # Independent finite-state DP; states are pattern prefixes, not whole names.
    from functools import lru_cache
    bad=c['forbidden_substrings']; endings=c['forbidden_suffixes']
    vowels=set(c['vowels']);blocked=set(c['forbidden_letters'])
    prefixes={''}
    for word in bad+endings:
        prefixes.update(word[:i] for i in range(1,len(word)+1))
    @lru_cache(None)
    def transition(state,ch):
        t=state+ch
        if any(t.endswith(s) for s in bad):return None
        return max((s for s in prefixes if t.endswith(s)),key=len)
    @lru_cache(None)
    def dp(left,state,last,cr,vr,same):
        if not left:return int(not any(state.endswith(s) for s in endings))
        total=0
        for ch in c['alphabet']:
            if ch in blocked:continue
            v=ch in vowels;nc=0 if v else cr+1;nv=vr+1 if v else 0;nr=same+1 if last==ch else 1
            if nc>c['max_consecutive_consonants'] or nv>c['max_consecutive_vowels'] or nr>c['max_identical_run']:continue
            state2=transition(state,ch)
            if state2 is not None:total+=dp(left-1,state2,ch,nc,nv,nr)
        return total
    def valid(word):
        cr=vr=same=0;last=''
        for ch in word:
            if ch in blocked:return False
            v=ch in vowels;cr=0 if v else cr+1;vr=vr+1 if v else 0;same=same+1 if ch==last else 1;last=ch
            if cr>c['max_consecutive_consonants'] or vr>c['max_consecutive_vowels'] or same>c['max_identical_run']:return False
        return not any(s in word for s in bad) and not any(word.endswith(s) for s in endings)
    return {n:dp(n,'','',0,0,0)-sum(len(w)==n and valid(w) for w in set(stop)) for n in c['lengths']}

def verify(args):
    db=connect(args.db);snap=json.loads(db.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0]);c=snap['rules'];start=time.time()
    integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
    counts={r[0]:r[1] for r in db.execute('SELECT length,count(*) FROM candidates GROUP BY length')}
    expected=expected_counts(c,snap['stop_words'])
    for n in c['lengths']: counts.setdefault(n,0)
    clauses=['length(name)!=length','length!=vowels+consonants','length(structure)!=length',"first!=substr(name,1,1)","last!=substr(name,-1)","name GLOB '*[^A-Z]*'"]
    params=[]
    for ch in c['forbidden_letters']:clauses.append('instr(name,?)>0');params.append(ch)
    for s in c['forbidden_substrings']:clauses.append('instr(name,?)>0');params.append(s)
    for s in c['forbidden_suffixes']:clauses.append('substr(name,-?)=?');params.extend([len(s),s])
    for ch,k in [('C','max_consecutive_consonants'),('V','max_consecutive_vowels')]:clauses.append('instr(structure,?)>0');params.append(ch*(c[k]+1))
    for ch in c['alphabet']:clauses.append('instr(name,?)>0');params.append(ch*(c['max_identical_run']+1))
    invalid=db.execute('SELECT count(*) FROM candidates WHERE '+' OR '.join(clauses),params).fetchone()[0]
    stop_matches=sum(db.execute('SELECT count(*) FROM candidates WHERE name=?',(w,)).fetchone()[0] for w in snap['stop_words'])
    keys=['raw','accepted','forbidden_letter','consonant_run','vowel_run','identical_run','forbidden_substring','forbidden_suffix','stop_match']
    row=db.execute('SELECT '+','.join('coalesce(sum('+x+'),0)' for x in keys)+' FROM checkpoints').fetchone();stats=dict(zip(keys,row))
    shards=db.execute('SELECT count(*) FROM checkpoints').fetchone()[0]
    raw=sum(26**n for n in c['lengths'])
    ok=integrity=='ok' and counts==expected and invalid==stop_matches==0 and shards==26*len(c['lengths']) and stats['raw']==raw and stats['accepted']==sum(counts.values()) and sum(stats[k] for k in keys[1:])==raw
    report={'passed':ok,'integrity_check':integrity,'actual_by_length':counts,'independent_expected_by_length':expected,'invalid_rows':invalid,'stop_matches':stop_matches,'duplicates':'Impossible under UNIQUE(name), checked by integrity_check','completed_shards':shards,'statistics':stats,'verification_seconds':time.time()-start,'database_bytes':pathlib.Path(args.db).stat().st_size,'snapshot':snap}
    out=pathlib.Path(args.report);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='snapshot'},ensure_ascii=False,indent=2));return 0 if ok else 1

def export_rows(db,where,params,directory,split=1000000,order='name ASC'):
    directory=pathlib.Path(directory);directory.mkdir(parents=True,exist_ok=True)
    cur=db.execute('SELECT c.*, (f.candidate_id IS NOT NULL) AS favorite FROM candidates c LEFT JOIN favorites f ON f.candidate_id=c.id '+where+' ORDER BY '+order,params)
    columns=[x[0] for x in cur.description];count=0;files=[];file=None
    try:
        for row in cur:
            if count%split==0:
                if file:file.close()
                path=directory/f'candidates_{len(files)+1:05}.csv'
                # Exclusive create prevents accidental overwrite of earlier exports.
                file=path.open('x',encoding='utf-8-sig',newline='');files.append(str(path));writer=csv.writer(file);writer.writerow(columns)
            writer.writerow(tuple(row));count+=1
    finally:
        if file:file.close()
    return {'rows':count,'files':files}

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    gen=sub.add_parser('generate');gen.add_argument('--config',default=str(ROOT/'config/rules.json'));gen.add_argument('--db',default=str(ROOT/'data/brands.sqlite'))
    ver=sub.add_parser('verify');ver.add_argument('--db',default=str(ROOT/'data/brands.sqlite'));ver.add_argument('--report',default=str(ROOT/'reports/verification.json'))
    exp=sub.add_parser('export');exp.add_argument('--db',default=str(ROOT/'data/brands.sqlite'));exp.add_argument('--out',required=True);exp.add_argument('--split',type=int,default=1000000);exp.add_argument('--filters',default='{}')
    serve=sub.add_parser('serve');serve.add_argument('--db',default=str(ROOT/'data/brands.sqlite'));serve.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    if args.command=='generate':return generate(args)
    if args.command=='verify':return verify(args)
    if args.command=='export':
        from server import query_filters
        if args.split<1:parser.error('--split must be positive')
        where,params,order=query_filters(json.loads(args.filters));db=connect(args.db);print(json.dumps(export_rows(db,where,params,args.out,args.split,order),ensure_ascii=False));db.close()
    if args.command=='serve':
        from server import serve
        serve(args.db,args.port)
    return 0
if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,sqlite3.Error,FileNotFoundError) as e:sys.exit(str(e))
