"""Read-only integrity check without printing any sealed path or task detail."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parent.parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify():
    frozen = json.loads((ROOT / 'FREEZE.json').read_text())
    records = frozen['files']
    mismatches = 0
    for row in records:
        path = REPOSITORY / row['path']
        if not path.is_file() or path.stat().st_size != row['bytes'] or sha(path) != row['sha256']:
            mismatches += 1
    present = {str(path.relative_to(REPOSITORY)) for path in ROOT.rglob('*')
               if path.is_file() and path.name != 'FREEZE.json'}
    listed = {row['path'] for row in records if row['path'].startswith('evaluation/context_mission_v2/')}
    extras = len(present - listed)
    canonical = json.dumps(records, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    inventory_ok = hashlib.sha256(canonical).hexdigest() == frozen['inventory_sha256']
    receipt = json.loads((REPOSITORY / 'evidence/context_mission_v2/freeze-summary.json').read_text())
    receipt_ok = sha(ROOT / 'FREEZE.json') == receipt['freeze_sha256']
    return {'passed': not mismatches and not extras and inventory_ok and receipt_ok,
            'files': len(records), 'mismatches': mismatches, 'unexpected_suite_files': extras,
            'inventory_hash_valid': inventory_ok, 'public_receipt_matches': receipt_ok}


if __name__ == '__main__':
    result = verify()
    print(json.dumps(result))
    raise SystemExit(0 if result['passed'] else 1)
