"""Read-only inventory verification; prints no sealed paths or semantics."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parent.parent

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def verify():
    frozen = json.loads((ROOT / 'FREEZE.json').read_text())
    records = frozen['files']
    mismatches = sum(not (REPOSITORY / item['path']).is_file() or
                     digest(REPOSITORY / item['path']) != item['sha256'] for item in records)
    present = {str(path.relative_to(REPOSITORY)) for path in ROOT.rglob('*') if path.is_file() and path.name != 'FREEZE.json'}
    expected = {item['path'] for item in records if item['path'].startswith('evaluation/context_mission_v1/')}
    extras = len(present - expected)
    canonical = json.dumps(records, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    inventory_ok = hashlib.sha256(canonical).hexdigest() == frozen['inventory_sha256']
    summary = json.loads((REPOSITORY / 'evidence/context_mission_v1/freeze-summary.json').read_text())
    receipt_ok = digest(ROOT / 'FREEZE.json') == summary['freeze_sha256']
    return {'passed': not mismatches and not extras and inventory_ok and receipt_ok,
            'files': len(records), 'mismatches': mismatches, 'unexpected_suite_files': extras,
            'inventory_hash_valid': inventory_ok, 'public_receipt_matches': receipt_ok}

if __name__ == '__main__':
    result = verify()
    print(json.dumps(result))
    raise SystemExit(0 if result['passed'] else 1)
