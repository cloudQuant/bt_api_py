"""MIG04 r3 commit-time supplement: archive re-reproduction + clean-cwd AFI/consumer origin checks.

Closes two traceability gaps found while preparing the formal round:
1. logs/01-archive.log is a 0-byte buffer file (archive exit codes not individually
   captured). Re-archive every pin NOW into a fresh supplement dir and compare
   sha256 against logs/01b-archive-sha256.txt (byte identity = closure).
2. logs/45-afi-import-app.log ran `import app` with cwd inside the AFI extraction,
   so it resolved ../src/afi/src/backend/app (cwd shadowing), NOT the installed wheel.
   Re-run origin checks from a clean cwd in the same consumers venv.

Writes only under D:/temp/it10mig04-r3-20261016-r1-supplement/. Does not touch
repos, the frozen r3 evidence dir, or any worktree (git archive reads objects only;
all -o paths are absolute intentionally).
"""
import hashlib
import subprocess
import sys
from pathlib import Path

EVID = Path('D:/temp/it10mig04-r3-20261016-r1')
SUP = Path('D:/temp/it10mig04-r3-20261016-r1-supplement')
ARCDIR = SUP / 'arc'
VENV = EVID / 'venvs/consumers/Scripts/python.exe'

PINS = {
    'sdk': ('D:/bt_api_py', '466977392985ef1d7fa9b282a220096c8d19dff8'),
    'base': ('D:/bt_api_py/bt_api/bt_api_base', 'b4aabd1e17b9c7252fa7b4d266932087d99b5517'),
    'transport_zmq': ('D:/bt_api_py/bt_api/bt_api_transport_zmq', 'a397fdb460d2deb22e67ceb4963992ef63dbbb0e'),
    'gateway': ('D:/bt_api_py/bt_api/bt_api_gateway', '59b164ab7f89c6428ae6546103aad6058dab4c16'),
    'execution': ('D:/bt_api_py/bt_api/bt_api_execution', 'dc8bd7a3682d00cfaef4f15261607043c6d24fca'),
    'risk': ('D:/bt_api_py/bt_api/bt_api_risk', '131175a4070c930f1dd5186ea707d6bc9bbb4310'),
    'monitor': ('D:/bt_api_py/bt_api/bt_api_monitor', '536a4f04747182749fabdac5e28624f80465bc53'),
    'binance': ('D:/bt_api_py/bt_api/bt_api_binance', 'b438a277c92a22851a16be0be1a8c8ee9f852b79'),
    'backtrader': ('D:/source_code/backtrader', 'b002afa2da66ca6d14f1623280109be8e653a1b6'),
    'afi': ('D:/source_code/ai-for-investor', '34f59117ac9a2e73f00b6908cfeeb11fa53f5610'),
}


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True)


def main():
    assert not SUP.exists() or not any(SUP.iterdir()), 'supplement dir must be absent or empty'
    ARCDIR.mkdir(parents=True, exist_ok=True)

    expected = {}
    for line in (EVID / 'logs/01b-archive-sha256.txt').read_text(encoding='utf-8').splitlines():
        digest, name = line.split(' ', 1)
        expected[name.lstrip('*').split('/')[-1][:-4]] = digest
    assert set(expected) == set(PINS), (set(expected) ^ set(PINS))

    lines = [
        'MIG04 r3 commit-time supplement A: archive re-reproduction',
        'command shape: git -C <repo> archive -o <abs out.tar> <pin>',
        'git --version: ' + run(['git', '--version']).stdout.decode().strip(),
        '',
    ]
    all_ok = True
    for name, (repo, pin) in PINS.items():
        out = ARCDIR / f'{name}.tar'
        p = run(['git', '-C', repo, 'archive', '-o', str(out), pin])
        assert p.returncode == 0, (name, p.stderr)
        actual = sha(out)
        ok = actual == expected[name]
        all_ok &= ok
        lines.append(f'{name:14s} pin={pin} expected={expected[name]} actual={actual} match={ok}')
        out.unlink()
    lines.append('')
    lines.append(f'ALL_MATCH={all_ok} (10/10 pins re-archived at commit time, byte-identical to logs/01b-archive-sha256.txt)')
    (SUP / 'archive-repro.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines))
    assert all_ok, 'archive reproduction mismatch'

    # Supplement B: origin checks from a clean cwd (supplement dir itself).
    probe = (
        "import json,sys\n"
        "import app, backtrader, bt_api_binance, bt_api_py\n"
        "print(json.dumps({\n"
        "  'sys_prefix': sys.prefix,\n"
        "  'app': app.__file__,\n"
        "  'backtrader': backtrader.__file__,\n"
        "  'bt_api_binance': bt_api_binance.__file__,\n"
        "  'bt_api_py': bt_api_py.__file__,\n"
        "}, ensure_ascii=False, indent=2))\n"
    )
    cmd = [str(VENV), '-c', probe]
    p = run(cmd, cwd=str(SUP))
    assert p.returncode == 0, p.stderr
    blob = p.stdout.decode()
    assert 'site-packages' in blob and 'src' not in blob.split('app')[1][:80], blob
    header = (
        'MIG04 r3 commit-time supplement B: clean-cwd origin checks (consumers venv)\n'
        'cwd: ' + str(SUP) + '  (outside all repos and all archive extractions)\n'
        'command: ' + ' '.join(cmd[:1]) + ' -c "<import app, backtrader, bt_api_binance, bt_api_py; print json __file__>"\n'
        'exit: ' + str(p.returncode) + '\n'
        'stderr: ' + repr(p.stderr.decode()) + '\n'
        '--- stdout ---\n'
    )
    (SUP / '45b-consumers-origins-clean-cwd.log').write_text(header + blob, encoding='utf-8')
    print(header + blob)
    print('SUPPLEMENT_OK')


if __name__ == '__main__':
    sys.exit(main())
