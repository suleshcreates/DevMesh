"""Disposable real-agent/operator lab. No fake agents, automatic edits or cleanup."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.git_workspace import GitWorkspace, process
from backend.main import create_app

async def prepare(directory):
    repo = directory / 'repository'
    repo.mkdir()
    code, _, _ = await process(['git', 'init', '-b', 'main'], repo)
    if code:
        raise SystemExit('Unable to initialize disposable repository')
    (repo / '.gitignore').write_text('/.worktrees/\n__pycache__/\n', encoding='utf-8')
    (repo / 'tests').mkdir()
    (repo / 'tests' / 'test_login.py').write_text('import unittest\nfrom login import authenticate\nclass LoginTest(unittest.TestCase):\n    def test_valid(self):\n        self.assertTrue(authenticate("demo", "example"))\n    def test_invalid(self):\n        self.assertFalse(authenticate("other", "wrong"))\n', encoding='utf-8')
    git = GitWorkspace(repo)
    await git.git('add', '.')
    await git.git('commit', '-m', 'Disposable login acceptance tests')
    await git.git('switch', '-c', 'operator')
    return repo

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--port', type=int, default=8425)
    args = parser.parse_args()
    if args.directory:
        directory = args.directory.resolve()
        if directory.exists():
            parser.error('Directory already exists; refusing to overwrite. Use an explicit restart command for an existing lab.')
        directory.mkdir(parents=True)
    else:
        directory = Path(tempfile.mkdtemp(prefix='devmesh-m5-manual-'))
    repo = asyncio.run(prepare(directory))
    command = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests']
    print('Disposable directory:', directory, flush=True)
    print('Execution repository:', repo, flush=True)
    print('Database:', directory / 'state.sqlite', flush=True)
    print('Test command JSON:', json.dumps(command), flush=True)
    print('Goal: Build a small local login feature: login.py exports authenticate(name, supplied), demo/example succeeds, other/wrong fails; add a login.html form. No real credentials or deployment.', flush=True)
    print('This lab uses real read-only Codex inference only after project start. Execution is a manual operator handoff; Antigravity must be activated separately.', flush=True)
    if args.port != 8420:
        print('REST-only lab port. The dashboard/bridge use 8420. For those checks, stop the normal API in its owning terminal and launch a new disposable lab with --port 8420.', flush=True)
    import uvicorn
    uvicorn.run(create_app(db_path=directory / 'state.sqlite'), host='127.0.0.1', port=args.port, access_log=False)

if __name__ == '__main__':
    main()
