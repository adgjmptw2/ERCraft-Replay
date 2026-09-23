"""Offline source-integrity and publication-boundary checks; no service imports."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT / 'SOURCE_MANIFEST.json').read_bytes())
errors = []
seen = set()
for row in manifest['files']:
    relative = row['path']
    target = ROOT / relative
    if relative in seen or not target.resolve().is_relative_to(ROOT):
        errors.append(f'invalid manifest path: {relative}')
        continue
    seen.add(relative)
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != row['sha256']:
        errors.append(f'source hash mismatch: {relative}')

files = subprocess.check_output(
    ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT
).decode('utf-8').split('\0')
for relative in filter(None, files):
    path = ROOT / relative
    if path.suffix.lower() in {'.er', '.zip', '.sqlite', '.sqlite3', '.db', '.pem', '.key', '.pfx', '.p12', '.dpapi'}:
        errors.append(f'private/generated file: {relative}')
    if path.name.startswith('.env') or path.name == 'config.local':
        errors.append(f'local configuration: {relative}')
    if path.suffix == '.py':
        try:
            ast.parse(path.read_text(encoding='utf-8-sig'), filename=relative)
        except (SyntaxError, UnicodeError) as exc:
            errors.append(f'Python syntax: {relative}: {exc}')
    if path.suffix == '.json':
        try:
            json.loads(path.read_bytes())
        except (ValueError, UnicodeError) as exc:
            errors.append(f'JSON syntax: {relative}: {exc}')
    if path.suffix in {'.py', '.json', '.js', '.mjs', '.md', '.html', '.css'}:
        text = path.read_text(encoding='utf-8-sig')
        # Report file names only, never matched secret values.
        patterns = [r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                    r'gh[pousr]_[A-Za-z0-9]{30,}', r'github_pat_[A-Za-z0-9_]{30,}',
                    r'AIza[A-Za-z0-9_-]{30,}']
        if any(re.search(pattern, text) for pattern in patterns):
            errors.append(f'credential-shaped content: {relative}')

if errors:
    raise SystemExit('\n'.join(errors))
print(f'PASS: {len(seen)} source hashes; publication file types, Python and JSON syntax checked.')
