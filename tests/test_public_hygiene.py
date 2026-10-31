"""Check publishable text for credential patterns and prohibited terminology."""
import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {
    'b95ee4efc49878ebc6e8f93c29d7f692742dad7330574634428c8d6355794d88',
    '9ddc400d8b49efc76c3b43507d157875b8423b56345ef02913fb193f85e72ee4',
    'bf4bf68699dea329620d6575954cac45eb87f30c33c44159ffe9be879afe47d7',
    '323cd769a5473958796fe5cc80f932b53fa41d170f8807bf430cd42b54d00666',
    '8bdb247a2a76e166450e193f185f0deeceedee6550589de2cd964e7ce36c9de6',
    '9b89025ce7a6d932b28f6e15132a70d402f723874a425e9b4c7cc3b179fa66ce',
    '33c186d117a72fbde2c59fd9cb6253e61ce139623f013181cfdb231df4e8af7d',
    'e305e9e079b463edccbc45d33cf685f497d3c9d2b3996178d5f41e3ead50b045',
    'b0ee2b7c5d55e9062728825865ac279029af5145bc16021d69216abbbe2884aa',
    '06e55b633481f7bb072957eabcf110c972e86691c3cfedabe088024bffe42f23',
}
PATTERNS = [
    re.compile('sk-' + r'ant-'),
    re.compile('sk-' + r'or-v1-'),
    re.compile('f' + r'c-[a-fA-F0-9]{32}'),
    re.compile(r'\b\d{8,12}:[A-Za-z0-9_-]{35}\b'),
    re.compile('AK' + r'IA[A-Z0-9]{16}'),
    re.compile('-{5}BEGIN ' + r'(?:RSA |EC |OPENSSH )?PRIVATE KEY-{5}'),
    re.compile(r'\b[A-Za-z]:\\(?:[^\s"\']+)'),
]


def test_public_hygiene():
    files = subprocess.check_output(['git', 'ls-files', '-co', '--exclude-standard', '-z'], cwd=ROOT).split(b'\0')
    failures = []
    for entry in files:
        if not entry:
            continue
        path = ROOT / entry.decode('utf-8')
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, PermissionError, OSError):
            continue
        tokens = re.findall(r'[a-z0-9]+', content.lower())
        for index, token in enumerate(tokens):
            candidates = [token]
            if index + 1 < len(tokens):
                candidates.append(token + ' ' + tokens[index + 1])
            if any(hashlib.sha256(value.encode()).hexdigest() in FORBIDDEN for value in candidates):
                failures.append(f'{path.relative_to(ROOT)}: prohibited terminology')
                break
        if any(pattern.search(content) for pattern in PATTERNS):
            failures.append(f'{path.relative_to(ROOT)}: credential or absolute path pattern')
    assert not failures, '\n'.join(failures)
