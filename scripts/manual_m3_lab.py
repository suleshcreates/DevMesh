"""Start an isolated operator lab. Existing directories are never reused or removed."""
import argparse
import asyncio
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.git_workspace import GitWorkspace
from backend.main import create_app

async def setup(directory):
    directory = Path(directory).resolve()
    # Refuse the implementation repository and all existing paths before any write.
    if directory.exists():
        raise ValueError('Lab directory must not already exist')
    directory.mkdir(parents=True)
    repository = directory / 'repository'
    repository.mkdir()
    git = GitWorkspace(repository)
    await git.git('init', '-b', 'main')
    (repository / '.gitignore').write_text('/.worktrees/\n', encoding='utf-8')
    (repository / 'base.txt').write_text('Disposable M3 lab\n', encoding='utf-8')
    await git.git('add', '.')
    await git.git('commit', '-m', 'Initialize disposable lab')
    await git.git('switch', '-c', 'operator')
    return directory, repository

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path(tempfile.gettempdir()) / ('devmesh-m3-' + uuid4().hex))
    parser.add_argument('--port', type=int, default=8421)
    args = parser.parse_args()
    directory, repository = asyncio.run(setup(args.directory))
    command = [sys.executable, '-c', 'from pathlib import Path; assert Path("a.txt").read_text().strip() == "hello"; print("lab tests passed")']
    app = create_app(db_path=directory / 'state.sqlite', repository=repository, test_command=command)
    print(f'Lab directory: {directory}')
    print(f'Repository: {repository}')
    print(f'API: http://127.0.0.1:{args.port}')
    print('Ctrl+C stops the server. Lab files and branches remain for inspection.')
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port, workers=1, access_log=False)

if __name__ == '__main__':
    main()
