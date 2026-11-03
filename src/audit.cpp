// Independent full-table audit: derive classification and rank from each stored name.
#include <sqlite3.h>
#include <chrono>
#include <iostream>
#include <string>
#include <stdexcept>
#include <vector>
using namespace std;
int main(int argc,char** argv){try{
 if(argc!=2)return 2;sqlite3* db=nullptr;if(sqlite3_open_v2(argv[1],&db,SQLITE_OPEN_READWRITE,nullptr)!=SQLITE_OK)return 2;
 sqlite3_busy_timeout(db,30000);sqlite3_exec(db,"PRAGMA query_only=ON",nullptr,nullptr,nullptr);
 auto vals=[&](string k){vector<string> r;sqlite3_stmt* s;if(sqlite3_prepare_v2(db,"SELECT value FROM rules WHERE kind=? ORDER BY ordinal",-1,&s,nullptr)!=SQLITE_OK)throw runtime_error(sqlite3_errmsg(db));sqlite3_bind_text(s,1,k.c_str(),-1,SQLITE_TRANSIENT);while(sqlite3_step(s)==SQLITE_ROW)r.emplace_back((const char*)sqlite3_column_text(s,0));sqlite3_finalize(s);return r;};
 string vowels=vals("vowels").at(0),banned=vals("forbidden_letters").at(0);int maxC=stoi(vals("max_consecutive_consonants")[0]),maxV=stoi(vals("max_consecutive_vowels")[0]),maxR=stoi(vals("max_identical_run")[0]);auto sub=vals("forbidden_substrings"),suffix=vals("forbidden_suffixes");
 sqlite3_stmt* s;if(sqlite3_prepare_v2(db,"SELECT id,name,length,first,last,reversed,vowels,consonants,structure,syllables FROM candidates",-1,&s,nullptr)!=SQLITE_OK)throw runtime_error(sqlite3_errmsg(db));
 long long rows=0,invalid=0;auto start=chrono::steady_clock::now();int rc;
 while((rc=sqlite3_step(s))==SQLITE_ROW){
  auto text=[&](int col){return string((const char*)sqlite3_column_text(s,col));};string w=text(1),pattern;int n=w.size(),nv=0,syllables=0;bool prev=false,ok=true;long long rank=0,offset=0,power=26;
  for(int k=1;k<n;k++){offset+=power;power*=26;}
  for(char ch:w){if(ch<'A'||ch>'Z'||banned.find(ch)!=string::npos)ok=false;bool v=vowels.find(ch)!=string::npos;nv+=v;syllables+=v&&!prev;prev=v;pattern+=v?'V':'C';rank=rank*26+ch-'A';}
  if(n!=sqlite3_column_int(s,2)||nv!=sqlite3_column_int(s,6)||n-nv!=sqlite3_column_int(s,7)||pattern!=text(8)||syllables!=sqlite3_column_int(s,9)||offset+rank+1!=sqlite3_column_int64(s,0)||text(3)!=w.substr(0,1)||text(4)!=w.substr(n-1)||text(5)!=string(w.rbegin(),w.rend()))ok=false;
  if(pattern.find(string(maxC+1,'C'))!=string::npos||pattern.find(string(maxV+1,'V'))!=string::npos)ok=false;
  for(char ch='A';ch<='Z';ch++)if(w.find(string(maxR+1,ch))!=string::npos)ok=false;
  for(auto t:sub)if(w.find(t)!=string::npos)ok=false;
  for(auto t:suffix)if(n>=t.size()&&w.substr(n-t.size())==t)ok=false;
  if(!ok)invalid++;rows++;
 }
 sqlite3_finalize(s);sqlite3_close(db);if(rc!=SQLITE_DONE)return 2;
 cout<<"{\"rows_checked\":"<<rows<<",\"invalid_rows\":"<<invalid<<",\"seconds\":"<<chrono::duration<double>(chrono::steady_clock::now()-start).count()<<",\"passed\":"<<(invalid?"false":"true")<<"}"<<endl;return invalid?1:0;
}catch(exception&e){cerr<<e.what()<<endl;return 2;}}
