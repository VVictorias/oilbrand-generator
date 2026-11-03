import copy, itertools, json, pathlib, sqlite3, subprocess, sys, tempfile, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import brandgen
from server import query_filters
BASE=json.loads((ROOT/'config/rules.json').read_text())
def reference(word,c,stop=()):
    if any(x in word for x in c['forbidden_letters']):return False
    types=''.join('V' if x in c['vowels'] else 'C' for x in word)
    if 'C'*(c['max_consecutive_consonants']+1) in types:return False
    if 'V'*(c['max_consecutive_vowels']+1) in types:return False
    if any(x*(c['max_identical_run']+1) in word for x in c['alphabet']):return False
    return not any(s in word for s in c['forbidden_substrings']) and not any(word.endswith(s) for s in c['forbidden_suffixes']) and word not in stop
class ProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.binary=brandgen.build()
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.p=pathlib.Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def config(self,**kw):
        c=copy.deepcopy(BASE);c['lengths']=[4,5,6];c['forbidden_letters']=[x for x in c['alphabet'] if x not in 'ABEK'];c.update(kw);return c
    def run_case(self,c,stop=(),directory='case'):
        p=self.p/directory;p.mkdir();cfg=p/'rules.json'
        if stop:(p/'stop.txt').write_text('\n'.join(stop));c=copy.deepcopy(c);c['stop_dictionary_files']=['stop.txt']
        cfg.write_text(json.dumps(c));dbpath=p/'test.sqlite';brandgen.initialize(dbpath,cfg)
        stop=sorted({w.strip().upper() for w in stop if w.strip() and not w.strip().startswith('#')})
        subprocess.run([str(self.binary),str(dbpath)],stdout=subprocess.DEVNULL,check=True)
        db=brandgen.connect(dbpath);actual={r[0] for r in db.execute('SELECT name FROM candidates')}
        alphabet=''.join(x for x in c['alphabet'] if x not in c['forbidden_letters'])
        expected={''.join(t) for n in c['lengths'] for t in itertools.product(alphabet,repeat=n) if reference(''.join(t),c,stop)}
        self.assertEqual(actual,expected);self.assertEqual(len(actual),db.execute('SELECT count(*) FROM candidates').fetchone()[0])
        bylen={n:sum(len(w)==n for w in expected) for n in c['lengths']};self.assertEqual(bylen,brandgen.expected_counts(c,list(stop)))
        self.assertEqual(sum(26**n for n in c['lengths']),db.execute('SELECT sum(raw) FROM checkpoints').fetchone()[0]);db.close();return dbpath,cfg,expected
    def test_all_26_letters_length_four(self):
        c=copy.deepcopy(BASE);c['lengths']=[4]
        path,cfg,expected=self.run_case(c);self.assertEqual(len(expected),96730)
    def test_every_filter_independently_and_combined(self):
        cases=[{},dict(max_consecutive_consonants=1),dict(max_consecutive_vowels=1),dict(max_identical_run=1),dict(max_identical_run=0),dict(forbidden_substrings=['BE','AKA']),dict(forbidden_suffixes=['EK','A']),dict(forbidden_substrings=['AB'],forbidden_suffixes=['KE'],max_identical_run=1)]
        for i,change in enumerate(cases):
            with self.subTest(change=change):self.run_case(self.config(**change),directory=str(i))
    def test_exact_stop_dictionary_normalization_and_snapshot(self):
        path,cfg,expected=self.run_case(self.config(),['beka','BEKA',' abek ','# comment','KA'])
        self.assertNotIn('BEKA',expected);self.assertIn('BEKAB',expected)
        db=brandgen.connect(path);snap=json.loads(db.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0]);self.assertEqual(snap['stop_words'],['ABEK','BEKA','KA']);db.close()
    def test_soft_flags_do_not_remove_anything(self):
        c=self.config();c['analysis']['warning_suffixes']=['KA'];path,_,expected=self.run_case(c)
        db=brandgen.connect(path);r=db.execute("SELECT * FROM candidates WHERE name='BEKA'").fetchone();self.assertEqual(r['warning_suffixes'],'KA');self.assertEqual(r['structure'],'CVCV');self.assertEqual(r['transcription'],'БЭКА');self.assertEqual(r['syllables'],2);self.assertIn('ambiguous_reading',r['phonetics'])
        r=db.execute("SELECT * FROM candidates WHERE name='BEBE'").fetchone();self.assertEqual(r['repeated_pairs'],'BE:2');self.assertEqual(r['repeated_letters'],'B:2,E:2');db.close()
    def test_atomic_resume_after_failed_shard(self):
        c=self.config();cfg=self.p/'rules.json';cfg.write_text(json.dumps(c));path=self.p/'resume.sqlite';brandgen.initialize(path,cfg)
        db=brandgen.connect(path);db.execute("CREATE TRIGGER fail_shard BEFORE INSERT ON candidates WHEN new.first='B' BEGIN SELECT RAISE(ABORT,'simulated interruption'); END");db.commit();db.close()
        result=subprocess.run([str(self.binary),str(path)],capture_output=True);self.assertNotEqual(result.returncode,0)
        db=brandgen.connect(path);self.assertEqual(db.execute('SELECT count(*) FROM checkpoints').fetchone()[0],1);self.assertEqual(db.execute("SELECT count(*) FROM candidates WHERE first='B'").fetchone()[0],0);db.execute('DROP TRIGGER fail_shard');db.commit();db.close()
        subprocess.run([str(self.binary),str(path)],stdout=subprocess.DEVNULL,check=True)
        clean,_,expected=self.run_case(c)
        db=brandgen.connect(path);self.assertEqual({r[0] for r in db.execute('SELECT name FROM candidates')},expected)
        before=db.execute('SELECT count(*) FROM candidates').fetchone()[0];db.close();subprocess.run([str(self.binary),str(path)],stdout=subprocess.DEVNULL,check=True)
        db=brandgen.connect(path);self.assertEqual(before,db.execute('SELECT count(*) FROM candidates').fetchone()[0]);db.close()
    def test_changed_config_refused(self):
        path,cfg,_=self.run_case(self.config());c=json.loads(cfg.read_text());c['forbidden_suffixes']=['A'];cfg.write_text(json.dumps(c))
        with self.assertRaises(ValueError):brandgen.initialize(path,cfg)
    def test_filter_combinations_keyset_sort_and_split_export(self):
        path,_,expected=self.run_case(self.config());db=brandgen.connect(path)
        for sort in ['name','length','vowels','consonants','structure']:
            for direction in ['asc','desc']:
                filters={'prefix':'B','suffix':'A','exclude':'KK,EE','vowels_min':2,'sort':sort,'direction':direction};seen=[]
                while True:
                    where,p,order=query_filters(filters,True);rows=[dict(r) for r in db.execute('SELECT c.* FROM candidates c LEFT JOIN favorites f ON f.candidate_id=c.id '+where+' ORDER BY '+order+' LIMIT 7',p)]
                    if not rows:break
                    seen.extend(r['name'] for r in rows)
                    import base64
                    r=rows[-1];filters['cursor']=base64.urlsafe_b64encode(json.dumps([r[sort],r['name']]).encode()).decode()
                wanted={w for w in expected if w.startswith('B') and w.endswith('A') and 'KK' not in w and 'EE' not in w and sum(ch in BASE['vowels'] for ch in w)>=2}
                self.assertEqual(set(seen),wanted);self.assertEqual(len(seen),len(wanted))
        ids=[r[0] for r in db.execute('SELECT id FROM candidates LIMIT 5')];f={'ids':ids};where,p,order=query_filters(f);result=brandgen.export_rows(db,where,p,self.p/'export',2,order);self.assertEqual(result['rows'],5);self.assertEqual(len(result['files']),3)
        import csv
        exported=0
        for filename in result['files']:
            with open(filename,encoding='utf-8-sig') as stream:exported+=len(list(csv.reader(stream)))-1
        self.assertEqual(exported,5)
        candidate=ids[0];db.execute('INSERT INTO favorites(candidate_id) VALUES(?)',(candidate,));db.commit();where,p,order=query_filters({'favorites':True});self.assertEqual(db.execute('SELECT count(*) FROM candidates c LEFT JOIN favorites f ON f.candidate_id=c.id '+where,p).fetchone()[0],1);db.close()
    def test_unsafe_sort_rejected_and_search_is_literal(self):
        with self.assertRaises(ValueError):query_filters({'sort':'name; DROP TABLE candidates'})
        where,p,order=query_filters({'q':"%' OR 1=1 --"});self.assertIn('instr(c.name,?)',where);self.assertEqual(p,["%' OR 1=1 --"])
    def test_invalid_config_rejected(self):
        for changes in [{'lengths':[4,4]},{'forbidden_substrings':['']},{'max_identical_run':-1},{'alphabet':'AB'}]:
            c=self.config(**changes);cfg=self.p/'invalid.json';cfg.write_text(json.dumps(c))
            with self.assertRaises(ValueError):brandgen.load_config(cfg)
if __name__=='__main__':unittest.main(verbosity=2)
