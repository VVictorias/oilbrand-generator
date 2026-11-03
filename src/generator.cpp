#include <sqlite3.h>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>
using namespace std;
struct DB { sqlite3* p=nullptr; DB(const char* path){if(sqlite3_open(path,&p)!=SQLITE_OK)throw runtime_error("open database"); sqlite3_busy_timeout(p,30000);} ~DB(){sqlite3_close(p);} void exec(string s){char* e=nullptr;if(sqlite3_exec(p,s.c_str(),nullptr,nullptr,&e)!=SQLITE_OK){string m=e?e:"SQL error";sqlite3_free(e);throw runtime_error(m);}} };
struct Stmt {sqlite3_stmt* p; DB& db; Stmt(DB& d,string s):db(d){if(sqlite3_prepare_v2(d.p,s.c_str(),-1,&p,nullptr)!=SQLITE_OK)throw runtime_error(sqlite3_errmsg(d.p));}~Stmt(){sqlite3_finalize(p);}void str(int i,const string& s){sqlite3_bind_text(p,i,s.c_str(),-1,SQLITE_TRANSIENT);}void num(int i,int64_t n){sqlite3_bind_int64(p,i,n);}void done(){if(sqlite3_step(p)!=SQLITE_DONE)throw runtime_error(sqlite3_errmsg(db.p));sqlite3_reset(p);sqlite3_clear_bindings(p);} };
vector<string> values(DB& d,string kind){Stmt q(d,"SELECT value FROM rules WHERE kind=? ORDER BY ordinal");q.str(1,kind);vector<string> a;while(sqlite3_step(q.p)==SQLITE_ROW)a.emplace_back((const char*)sqlite3_column_text(q.p,0));return a;}
bool ends(const string&s,const string&t){return s.size()>=t.size()&&s.compare(s.size()-t.size(),t.size(),t)==0;}
int main(int argc,char**argv){try{
 if(argc<2)throw runtime_error("usage: generator DATABASE [--count-only]"); bool countOnly=argc>2&&string(argv[2])=="--count-only";
 DB db(argv[1]);db.exec("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA cache_size=-65536;");
 auto get=[&](string k){auto v=values(db,k);if(v.size()!=1)throw runtime_error("missing rule "+k);return v[0];};
 string alphabet=get("alphabet"),vowels=get("vowels"),forbidden=get("forbidden_letters");
 int maxC=stoi(get("max_consecutive_consonants")),maxV=stoi(get("max_consecutive_vowels")),maxR=stoi(get("max_identical_run"));
 auto subs=values(db,"forbidden_substrings"),suffixes=values(db,"forbidden_suffixes"),warnings=values(db,"warning_suffixes");
 string ambiguous=get("ambiguous_letters");auto stopV=values(db,"stop");unordered_set<string> stop(stopV.begin(),stopV.end());
 array<string,26> tr; for(int i=0;i<26;i++)tr[i]=get(string("tr_")+char('A'+i));
 Stmt insert(db,"INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)");
 uint64_t powers[7]={1};for(int i=1;i<=6;i++)powers[i]=powers[i-1]*26;
 uint64_t allAccepted=0,allRaw=0;auto globalStart=chrono::steady_clock::now();
 for(auto lenS:values(db,"lengths")){int len=stoi(lenS);uint64_t offset=0;for(int k=1;k<len;k++)offset+=powers[k];
 for(int first=0;first<26;first++){
 string shard=to_string(len)+":"+char('A'+first);Stmt check(db,"SELECT 1 FROM checkpoints WHERE shard=?");check.str(1,shard);if(!countOnly&&sqlite3_step(check.p)==SQLITE_ROW)continue;
 array<uint64_t,7> counts{};uint64_t accepted=0;auto start=chrono::steady_clock::now();if(!countOnly)db.exec("BEGIN IMMEDIATE");
 string word,pattern;
 auto dfs=[&](auto&& self,int depth,int cr,int vr,int same,uint64_t rank)->void{
  if(depth==len){
   for(auto&s:suffixes)if(ends(word,s)){counts[5]++;return;}
   if(stop.count(word)){counts[6]++;return;}
   accepted++;if(countOnly)return;
   int nv=0,groups=0;bool prev=false;string ru,letters,pairs,flags,ws;
   array<int,26> lc{};array<int,676> pc{};
   for(int i=0;i<len;i++){bool v=pattern[i]=='V';nv+=v;groups+=v&&!prev;prev=v;ru+=tr[word[i]-'A'];lc[word[i]-'A']++;if(i)pc[(word[i-1]-'A')*26+word[i]-'A']++;}
   for(int i=0;i<26;i++)if(lc[i]>1){if(!letters.empty())letters+=",";letters+=char('A'+i);letters+=":"+to_string(lc[i]);}
   for(int i=0;i<676;i++)if(pc[i]>1){if(!pairs.empty())pairs+=",";pairs+=char('A'+i/26);pairs+=char('A'+i%26);pairs+=":"+to_string(pc[i]);}
   auto flag=[&](string f){if(!flags.empty())flags+=",";flags+=f;};
   if(!nv)flag("no_vowels");if(pattern.find("CC")!=string::npos)flag("consonant_cluster");if(pattern.find("VV")!=string::npos)flag("vowel_cluster");if(word.find_first_of(ambiguous)!=string::npos)flag("ambiguous_reading");if(!letters.empty())flag("repeated_letters");
   for(auto&s:warnings)if(ends(word,s)){if(!ws.empty())ws+=",";ws+=s;}
   string rev(word.rbegin(),word.rend());
   insert.num(1,offset+rank+1);insert.str(2,word);insert.num(3,len);insert.str(4,word.substr(0,1));insert.str(5,word.substr(len-1));insert.str(6,rev);insert.num(7,nv);insert.num(8,len-nv);insert.str(9,pattern);insert.str(10,ru);insert.num(11,groups);insert.str(12,letters);insert.str(13,pairs);insert.str(14,ws);insert.str(15,flags);insert.num(16,word.find_first_of(ambiguous)!=string::npos);insert.done();return;
  }
  int lo=depth==0?first:0, hi=depth==0?first+1:26;
  for(int i=lo;i<hi;i++){char ch='A'+i;uint64_t weight=powers[len-depth-1];int reason=-1;
   bool v=vowels.find(ch)!=string::npos;int nc=v?0:cr+1,nv=v?vr+1:0,nr=!word.empty()&&word.back()==ch?same+1:1;
   if(forbidden.find(ch)!=string::npos)reason=0;else if(nc>maxC)reason=1;else if(nv>maxV)reason=2;else if(nr>maxR)reason=3;
   word+=ch;pattern+=v?'V':'C';
   if(reason<0)for(auto&s:subs)if(ends(word,s)){reason=4;break;}
   if(reason>=0)counts[reason]+=weight;else self(self,depth+1,nc,nv,nr,rank*26+i);
   word.pop_back();pattern.pop_back();
  }
 };
 dfs(dfs,0,0,0,0,0);uint64_t total=accepted;for(auto n:counts)total+=n;if(total!=powers[len-1])throw runtime_error("coverage mismatch");
 double seconds=chrono::duration<double>(chrono::steady_clock::now()-start).count();
 if(!countOnly){Stmt cp(db,"INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?,?,?,?,?)");cp.str(1,shard);cp.num(2,len);cp.num(3,total);cp.num(4,accepted);for(int i=0;i<7;i++)cp.num(5+i,counts[i]);cp.done();db.exec("COMMIT");}
 allAccepted+=accepted;allRaw+=total;cout<<shard<<" raw="<<total<<" accepted="<<accepted<<" seconds="<<seconds<<endl;
 }}
 cout<<"DONE raw="<<allRaw<<" accepted="<<allAccepted<<" seconds="<<chrono::duration<double>(chrono::steady_clock::now()-globalStart).count()<<endl;
 return 0;
 }catch(exception&e){cerr<<e.what()<<endl;return 1;}}
