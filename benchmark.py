"""Measure the native exhaustive filter traversal without writing candidates."""
import json,pathlib,resource,subprocess,time
from brandgen import ROOT,build
start=time.perf_counter()
with (ROOT/'reports/count_benchmark.log').open('w') as log:
    subprocess.run([str(build()),str(ROOT/'data/brands.sqlite'),'--count-only'],stdout=log,check=True)
usage=resource.getrusage(resource.RUSAGE_CHILDREN)
result={'wall_seconds':time.perf_counter()-start,'max_rss_platform_units':usage.ru_maxrss,'rss_unit':'bytes on macOS, KiB on Linux','scope':'filter traversal only; candidates and indexes are not written'}
(ROOT/'reports/count_benchmark_resources.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
