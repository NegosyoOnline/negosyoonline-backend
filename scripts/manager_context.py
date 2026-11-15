"""Build a compact historical task index and validate manager startup budgets.

Run with the repository Python. Never contacts ClickUp or changes approvals.
"""
import argparse
import re
import json
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'docs/session-state'
ARCHIVE = ROOT / 'docs/runbooks/rulings-archive'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--build-index', action='store_true')
    parser.add_argument('--build-rulings', action='store_true')
    args = parser.parse_args()
    source_path = ARCHIVE / 'rulings-source.json'
    if args.build_rulings and source_path.exists():
        source = json.loads(source_path.read_text())
        comments = source['comments']
        assert source['pagination_exhausted'], 'Rulings pagination incomplete'
        assert len({c['id'] for c in comments}) == len(comments), 'Duplicate ruling IDs'
        assert all(not c.get('reply_count') for c in comments), 'Replies must be fetched'
        rows = ['# Ruling coverage index', '',
                'All source comments fetched to exhaustion. Exact source: rulings-source.json.',
                'Read this index and the manager brief at startup; read exact source text for the topic before deciding.',
                'Newest source wins. Incorporated records are evidence, not additional authority.',
                'Partially superseded entries retain every clause not expressly replaced.', '']
        inventory = []
        for c in comments:
            kind = 'incorporation record' if 'now in the briefs' in c['comment_text'].split('\n')[0].lower() else 'source ruling; apply later amendments'
            if c['id'] in ('1100430000508189', '1100430000508109'):
                kind = 'closed one-time reorganization; no continuing approval'
            rows.append(f"- {c['id']}: {c['comment_text'].splitlines()[0]} [{kind}]")
            inventory.append({'id': c['id'], 'date': c['date'], 'classification': kind,
                              'sha256': hashlib.sha256(c['comment_text'].encode()).hexdigest()})
        (STATE / 'active-rulings.md').write_text('\n'.join(rows) + '\n')
        (ARCHIVE / 'rulings-inventory.json').write_text(json.dumps({
            'coverage': 'COMPLETE_SOURCE_INVENTORY', 'newest_comment_id': comments[0]['id'],
            'count': len(comments), 'entries': inventory}, indent=2) + '\n')
    if args.build_index:
        source = STATE / 'manager-task-map.md'
        entries = {}
        for number, line in enumerate(source.read_text().splitlines(), 1):
            if not line.startswith('#'):
                continue
            match = re.search(r'z8uq\w+', line)
            if match:
                entries.setdefault(match.group(), (number, line.lstrip('# ').strip()[:160]))
        rows = ['# Historical task locator', '',
                'Historical pointers only. Status, owner and dependencies require a current task read.',
                'Read only matching rows. Never interpret this index as current approval or status.', '']
        rows += [f'- {key}: {title}; source manager-task-map.md:{line}; current state UNVERIFIED'
                 for key, (line, title) in sorted(entries.items())]
        (STATE / 'manager-task-index.md').write_text('\n'.join(rows) + '\n')
    failed = False
    for name, limit in [('manager-now.md', 2500), ('manager-task-index.md', 5000),
                        ('manager-decisions.md', 1500)]:
        path = STATE / name
        count = len(path.read_text().split()) if path.exists() else 0
        ok = path.exists() and 0 < count <= limit
        print(f'{name}: {count}/{limit} words: {"PASS" if ok else "FAIL"}')
        failed |= not ok
    try:
        source = json.loads(source_path.read_text())
        inventory = json.loads((ARCHIVE / 'rulings-inventory.json').read_text())
        entries = {entry['id']: entry for entry in inventory['entries']}
        summary = (ROOT / 'docs/runbooks/manager-rulings-consolidated.md').read_text()
        for comment in source['comments']:
            assert comment['id'] in summary, f"Missing source ID {comment['id']}"
            assert entries[comment['id']]['sha256'] == hashlib.sha256(
                comment['comment_text'].encode()).hexdigest(), 'Archive hash mismatch'
        assert source['pagination_exhausted'], 'Archive pagination incomplete'
        print(f"Ruling archive: {len(source['comments'])} source IDs/hashes PASS")
    except (OSError, ValueError, KeyError, AssertionError) as error:
        print(f'Ruling archive FAIL: {error}')
        failed = True
    raise SystemExit(1 if failed else 0)


if __name__ == '__main__':
    main()
