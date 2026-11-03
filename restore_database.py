#!/usr/bin/env python3
"""Restore the complete release database from its gzip asset, checking SHA-256."""
import argparse,gzip,hashlib,os,pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parent

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('archive',type=pathlib.Path);p.add_argument('--out',type=pathlib.Path,default=ROOT/'data/brands.sqlite');args=p.parse_args()
    expected=(ROOT/'reports/database.sha256').read_text().split()[0]
    target=args.out.resolve();target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():raise ValueError('Destination already exists; choose another --out to preserve results and favorites')
    temporary=target.with_name(target.name+'.restoring');h=hashlib.sha256();total=0
    if temporary.exists():raise ValueError('Temporary restore file already exists; choose another --out')
    try:
        with gzip.open(args.archive,'rb') as source,temporary.open('xb') as out:
            while chunk:=source.read(8*1024*1024):out.write(chunk);h.update(chunk);total+=len(chunk)
        if h.hexdigest()!=expected:raise ValueError('SHA-256 mismatch: archive does not restore the verified release database')
        # Hard-link creation refuses an output that appeared during extraction.
        os.link(temporary,target);temporary.unlink()
    except Exception:
        temporary.unlink(missing_ok=True);raise
    print(f'Restored {target}: {total:,} bytes; SHA-256 verified')
if __name__=='__main__':
    try:main()
    except (OSError,ValueError,EOFError) as exc:sys.exit(str(exc))
