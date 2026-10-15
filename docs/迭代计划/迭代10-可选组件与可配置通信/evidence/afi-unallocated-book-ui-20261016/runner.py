import hashlib
import json
import os
import subprocess
from pathlib import Path
from datetime import datetime, timezone

repo = Path('D:/source_code/ai-for-investor')
cwd = repo / 'src/frontend'
output = Path('D:/temp/iteration10-afi-unallocated-ui-root-20261016-r1')
pins = {
 'src/frontend/src/api/portfolio.ts': 'a076c8a2de63c664bd2a13fd7deb9fce4e0a23ad90e87b718e74e493f705268d',
 'src/frontend/src/views/PortfolioPage.vue': '623e0ea1186904004fbc626fbc2f140cb629f1d3f16098928252e6d49dd55277',
 'src/frontend/src/i18n/locales/en-US.ts': 'a0d7d81865c858a2ba1665618522c43bd3f6d2f3d7efa48e296336cd0a1d23db',
 'src/frontend/src/i18n/locales/zh-CN.ts': '51dd171d5ee1fe803dc7fcfd34b7c07382ee70ceba36016d23e2c119d829bd74',
 'src/frontend/src/__tests__/views/PortfolioPage.test.ts': '97abab02ef1c773fd733f56a2ff2e4c27427a98f508437fe1cb4515ee47a1a90',
}
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def git(*args): return subprocess.check_output(['git', '-C', str(repo), *args]).decode().strip()
def write(name, value):
    (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

assert not output.exists()
assert git('branch', '--show-current') == 'dev'
head = git('rev-parse', 'HEAD')
assert head == '2d081f4308215c4aa7f90d656039026fd9713e2e'
for path, pin in pins.items(): assert sha(repo / path) == pin, path
sources = {str(p): sha(p) for p in (cwd / 'src').rglob('*') if p.is_file() and p.suffix in ('.ts', '.vue', '.js', '.json', '.scss', '.css')}
for name in ('package.json', 'package-lock.json', 'vitest.config.ts', 'vite.config.ts', 'tsconfig.json'):
    path = cwd / name
    if path.exists(): sources[str(path)] = sha(path)
node = Path('C:/Program Files/nodejs/node.exe')
argv = [str(node), 'node_modules/vitest/vitest.mjs', 'run', 'src/__tests__/views/PortfolioPage.test.ts', '-t',
        'shows live unallocated books separately|keeps older live projections usable', '--reporter=default', '--reporter=json', '--outputFile=' + str(output / 'vitest.json')]
env = os.environ.copy()
env.update({'CI': '1', 'NO_COLOR': '1'})
output.mkdir(parents=True)
(output / 'runner.py').write_bytes(Path(__file__).read_bytes())
write('spec.json', {'source_sha256': sources, 'repositories': {str(repo): head}, 'selected_paths': pins, 'argv': argv, 'cwd': str(cwd)})
write('command.json', {'argv': argv, 'cwd': str(cwd), 'environment_overrides': {'CI': '1', 'NO_COLOR': '1'}, 'node_version': subprocess.check_output([str(node), '--version']).decode().strip(), 'started_utc': datetime.now(timezone.utc).isoformat()})
with (output / 'stdout.log').open('wb') as stdout, (output / 'stderr.log').open('wb') as stderr:
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
    print(json.dumps({'pid': process.pid, 'output': str(output)}), flush=True)
    exit_code = process.wait()
after = {p: sha(p) for p in sources}
after_head = git('rev-parse', 'HEAD')
result = {'vitest_exit': exit_code, 'sources_unchanged': sources == after, 'source_count': len(sources), 'heads_unchanged': head == after_head,
          'heads_before': {str(repo): head}, 'heads_after': {str(repo): after_head},
          'stdout_sha256': sha(output / 'stdout.log'), 'stderr_sha256': sha(output / 'stderr.log'),
          'evidence_level': 'VUE_COMPONENT_SYNTHETIC_API_UI_ONLY_NOT_COMPLETE_AFI_ACCEPTANCE'}
write('result.json', result)
print(json.dumps(result), flush=True)
assert sources == after and head == after_head
raise SystemExit(exit_code)
