"""Browser-only injected adapters and disposable data. Never imported by production."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import uvicorn
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from test_e2e_devmesh import FakeCodex, FakeAntigravity, make_repo
from backend.main import create_app

class BrowserCodex(FakeCodex):
    async def request(self, job, repository):
        self.challenge = job['phase'] == 'critique' and len(job['context'].get('design_decisions', [])) == 1
        return await super().request(job, repository)

class BrowserAntigravity(BrowserCodex, FakeAntigravity):
    pass

parser = argparse.ArgumentParser()
parser.add_argument('--directory', type=Path, required=True)
parser.add_argument('--port', type=int, default=18425)
args = parser.parse_args()
if not args.directory.is_absolute() or not args.directory.is_dir():
    raise SystemExit('Supply the new isolated browser fixture directory')
repo = args.directory / 'target with spaces'
if not repo.exists():
    asyncio.run(make_repo(args.directory))
command = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests']
(args.directory / 'fixture.json').write_text(json.dumps({'repository': str(repo), 'test_command': command}))
codex, antigravity = BrowserCodex(), BrowserAntigravity()
app = create_app(args.directory / 'state.sqlite', adapters={'codex': codex, 'antigravity': antigravity})
original_lifespan = app.router.lifespan_context
@asynccontextmanager
async def lifespan(application):
    async with original_lifespan(application):
        codex.system = antigravity.system = app.state.orchestrator
        yield
app.router.lifespan_context = lifespan
if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)
