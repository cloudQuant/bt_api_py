import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

root = Path('D:/bt_api_py')
out = Path('D:/temp/iteration10-reg01-root-edb-20261015-r4')
source = out/'source'
manifest = json.loads((out/'source-manifest.json').read_text(encoding='utf-8'))
def sha(raw): return hashlib.sha256(raw).hexdigest()
for path, record in manifest['files'].items():
    raw = (source/path).read_bytes()
    assert len(raw) == record['bytes'] and sha(raw) == record['sha256'], path
assert not (out/'run').exists()
run = out/'run'
run.mkdir()
packages = ['bt_api_base', 'bt_api_gateway', 'bt_api_risk', 'bt_api_transport_zmq', 'bt_api_execution', 'bt_api_monitor']
package_state = {}
for name in packages:
    repo = root/'bt_api'/name
    current = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD']).decode().strip()
    link = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'edb72152616629fbc6238b993f472f5f997717b2:bt_api/'+name]).decode().strip()
    assert current == link, name
    status = subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain', '--untracked-files=no']).decode()
    assert not status, name
    package_state[name] = {'head': current, 'tracked_clean': True}
env = os.environ.copy()
env.update(PYTHONPATH=os.pathsep.join([str(source), *(str(root/'bt_api'/name/'src') for name in packages)]), PYTHONUTF8='1', PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
node = 'tests/unit/test_service_registration_authorization_matrix.py::test_real_service_registration_returns_scoped_policy_receipts_and_denies_bypasses'
command = [sys.executable, '-P', '-m', 'pytest', '-vv', '--noconftest', '--import-mode=importlib', '-p', 'no:cacheprovider', '--basetemp', str(run/'pytest-tmp'), '--junitxml', str(run/'junit.xml'), node]
invocation = {'command': command, 'cwd': str(source), 'environment_overrides': {key: env[key] for key in ('PYTHONPATH', 'PYTHONUTF8', 'PYTHONNOUSERSITE', 'PYTHONDONTWRITEBYTECODE', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD')}, 'source_manifest_sha256': sha((out/'source-manifest.json').read_bytes()), 'packages': package_state, 'scope': 'one REG01 local synthetic-provider integration node'}
invocation['repository_head_at_launch'] = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip()
assert invocation['repository_head_at_launch'] == '4861237c83765682a1c77324c23670290b550100'
(run/'invocation.json').write_text(json.dumps(invocation, indent=2)+'\n', encoding='utf-8')
started = time.monotonic()
with (run/'stdout.log').open('xb') as stdout, (run/'stderr.log').open('xb') as stderr:
    process = subprocess.Popen(command, cwd=source, env=env, stdout=stdout, stderr=stderr)
    (run/'process.json').write_text(json.dumps({'pid':process.pid,'started':True})+'\n', encoding='utf-8')
    returncode = process.wait()
(run/'exit-code.txt').write_text(str(returncode)+'\n', encoding='utf-8')
changed = []
for path, record in manifest['files'].items():
    if sha((source/path).read_bytes()) != record['sha256']:
        changed.append(path)
result = {'returncode': returncode, 'elapsed_seconds': round(time.monotonic()-started, 3), 'source_files_checked': len(manifest['files']), 'changed_inputs': changed, 'stdout_sha256':sha((run/'stdout.log').read_bytes()), 'stderr_sha256':sha((run/'stderr.log').read_bytes())}
package_changed = []
for name, state in package_state.items():
    repo = root/'bt_api'/name
    current = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD']).decode().strip()
    dirty = subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain', '--untracked-files=no'])
    if current != state['head'] or dirty:
        package_changed.append(name)
result['changed_packages'] = package_changed
(run/'result.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
print(json.dumps(result))
print((run/'stdout.log').read_text(encoding='utf-8', errors='replace')[-16000:])
if changed or package_changed:
    raise SystemExit(3)
raise SystemExit(returncode)
