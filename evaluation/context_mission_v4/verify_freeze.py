"""Read-only inventory/profile verification with no sealed-path disclosure."""
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
REPOSITORY=ROOT.parent.parent

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def verify():
    frozen=json.loads((ROOT/'FREEZE.json').read_text())
    rows=frozen['files']
    mismatches=0
    for row in rows:
        path=REPOSITORY/row['path']
        if not path.is_file() or path.stat().st_size!=row['bytes'] or sha(path)!=row['sha256']: mismatches+=1
    present={str(path.relative_to(REPOSITORY)) for path in ROOT.rglob('*') if path.is_file() and path.name!='FREEZE.json'}
    listed={row['path'] for row in rows if row['path'].startswith('evaluation/context_mission_v4/')}
    extras=len(present-listed)
    canonical=json.dumps(rows,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    inventory_ok=hashlib.sha256(canonical).hexdigest()==frozen['inventory_sha256']
    receipt=json.loads((REPOSITORY/'evidence/context_mission_v4/freeze-summary.json').read_text())
    receipt_ok=sha(ROOT/'FREEZE.json')==receipt['freeze_sha256']
    manifest=json.loads((ROOT/'manifest.json').read_text())
    profile_ok=manifest['runtime_profile']==receipt['runtime_profile']=={'experiment_profile':'thinking128k','model':'deepseek-flash'}
    return {'passed':not mismatches and not extras and inventory_ok and receipt_ok and profile_ok,
        'files':len(rows),'mismatches':mismatches,'unexpected_suite_files':extras,
        'inventory_hash_valid':inventory_ok,'public_receipt_matches':receipt_ok,'profile_matches':profile_ok}
if __name__=='__main__':
    result=verify()
    print(json.dumps(result))
    raise SystemExit(0 if result['passed'] else 1)
