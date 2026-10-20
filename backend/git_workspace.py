"""Git CLI workspaces; no shell interpolation and no destructive cleanup."""
import asyncio
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from backend.task_planner import fail
from backend.security_guard import redact

class GitError(Exception):
    pass

def validate_scope(value):
    value = value.replace('\\', '/')
    p = PurePosixPath(value)
    if (not value or value.startswith('/') or PureWindowsPath(value).drive or
        any(s in ('', '.', '..') for s in value.rstrip('/').split('/')) or
        any(c in value for c in '*?[:\x00\r\n') or
        any(ord(c) < 32 for c in value) or
        any(s.endswith((' ', '.')) or s.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(1,10)), *('LPT'+str(i) for i in range(1,10))} for s in p.parts) or
        any(s.lower() in ('.git', '.worktrees') for s in p.parts)):
        fail('Invalid scope path', 422)
    return value

def in_scope(path, scopes):
    path = validate_scope(path)
    # Case folding is conservative for overlap, matching Windows filesystem semantics.
    return any(path.casefold() == s.rstrip('/').casefold() or
               (s.endswith('/') and path.casefold().startswith(s.casefold())) for s in scopes)

async def process(args, cwd, *, stdin=None, timeout=600):
    env = dict(os.environ)
    # Prevent caller Git variables from redirecting repository/index writes.
    env = {k: v for k, v in env.items() if not k.startswith('GIT_')}
    env.update(GIT_TERMINAL_PROMPT='0', GIT_MERGE_AUTOEDIT='no')
    child = await asyncio.create_subprocess_exec(*args, cwd=str(cwd), env=env,
                stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(child.communicate(stdin.encode() if stdin is not None else None), timeout)
    finally:
        from scripts.spike_codex import shutdown
        warnings = await shutdown(child, asyncio.create_task(asyncio.sleep(0)))
        if warnings:
            raise GitError('Subprocess cleanup incomplete; operator inspection required')
    return child.returncode, out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace')

class GitWorkspace:
    def __init__(self, repository):
        self.root = Path(repository).resolve()
        self.worktrees = self.root / '.worktrees'

    async def git(self, *args, cwd=None, stdin=None):
        code, out, err = await process(['git', '-c', 'core.hooksPath=NUL' if os.name == 'nt' else 'core.hooksPath=/dev/null',
                                      '-c', 'commit.gpgSign=false', '-c', 'user.name=DevMesh integration', '-c', 'user.email=devmesh@localhost', *args], cwd or self.root, stdin=stdin)
        if code:
            # Output may contain filenames/diffs or secrets. Never expose raw stderr.
            raise GitError(redact('Git command failed: ' + args[0]))
        return out.rstrip('\r\n')

    async def validate(self):
        if not self.root.is_dir():
            fail('Repository does not exist', 422)
        top = Path(await self.git('rev-parse', '--show-toplevel')).resolve()
        if top != self.root:
            fail('Repository must be its top-level working tree', 422)
        await self.git('rev-parse', '--verify', 'refs/heads/main')
        if self.worktrees.is_symlink() or self.worktrees.resolve() != self.worktrees:
            fail('Unsafe worktrees directory')

    async def ensure_ignored(self):
        await self.validate()
        ignored, _, _ = await process(['git', 'check-ignore', '-q', '.worktrees/probe'], self.root)
        if ignored:
            fail('Add /.worktrees/ to repository .gitignore before workspace creation')

    def path(self, name):
        if not re.fullmatch(r'[A-Za-z0-9-]+', name):
            fail('Invalid workspace name')
        path = self.worktrees / name
        if path.resolve() != path or path.is_symlink():
            fail('Unsafe workspace path')
        return path

    async def registrations(self):
        raw = await self.git('worktree', 'list', '--porcelain')
        result, current = {}, None
        for line in raw.splitlines():
            if line.startswith('worktree '):
                current = Path(line[9:]).resolve()
            elif line.startswith('branch '):
                result[line[7:]] = current
        return result

    async def create(self, task):
        await self.ensure_ignored()
        branch = f"feat/{task['assigned_agent']}-{task['id']}"
        path = self.path(task['id'])
        refs = await self.registrations()
        if path.exists():
            if refs.get('refs/heads/' + branch) != path:
                fail('Existing worktree is not the task branch')
        elif 'refs/heads/' + branch in refs:
            fail('Task branch is checked out elsewhere')
        else:
            code, _, _ = await process(['git', 'show-ref', '--verify', '--quiet', 'refs/heads/' + branch], self.root)
            if code == 0:
                await self.git('worktree', 'add', str(path), branch)
            else:
                # Current verified implementation history, not necessarily main.
                await self.git('worktree', 'add', '-b', branch, str(path), 'HEAD')
        return branch, str(path)

    async def validate_task(self, task):
        await self.validate()
        expected = self.path(task['id'])
        branch = f"feat/{task['assigned_agent']}-{task['id']}"
        if task['branch'] != branch or task['worktree_path'] != str(expected):
            fail('Task workspace identity mismatch')
        if (await self.registrations()).get('refs/heads/' + branch) != expected:
            fail('Task workspace registration mismatch')
        return expected

    def write_path(self, task, relative):
        relative = validate_scope(relative)
        base = self.path(task['id'])
        target = (base / relative).resolve()
        if not target.is_relative_to(base) or not in_scope(relative, task['touched_files_scope']):
            fail('Write is outside assigned worktree or scope')
        return target

    async def head(self, branch):
        return await self.git('rev-parse', '--verify', 'refs/heads/' + branch)

    async def changed(self, branch):
        raw = await self.git('diff', '--name-only', '-z', '--no-renames', 'main...' + branch, '--')
        return [p for p in raw.split('\0') if p]

    async def check_scope(self, task):
        path = await self.validate_task(task)
        if await self.git('status', '--porcelain', '--untracked-files=all', cwd=path):
            fail('Task worktree must be clean')
        paths = await self.changed(task['branch'])
        for changed in paths:
            self.write_path(task, changed)
        # Git symlinks can write outside the worktree when followed; reject them.
        raw = await self.git('ls-tree', '-r', task['branch'])
        if any(line.startswith(('120000 ', '160000 ')) for line in raw.splitlines()):
            fail('Symlinks and submodules require manual isolation')
        return paths

    async def overlap(self, task, planner):
        changed = {p.casefold(): p for p in await self.changed(task['branch'])}
        blocked = False
        for other in await planner.list():
            if other['id'] == task['id'] or not other['branch'] or other['status'] == 'complete':
                continue
            common = sorted(changed[p] for p in changed.keys() & {v.casefold() for v in await self.changed(other['branch'])})
            if not common:
                continue
            if any(not in_scope(p, task['touched_files_scope']) or not in_scope(p, other['touched_files_scope']) for p in common):
                await planner.serialization(task, other, common)
                blocked = True
            elif other['id'] not in task['dependencies'] and task['id'] not in other['dependencies']:
                # Even intentional overlaps need explicit dependency ordering.
                await planner.serialization(task, other, common)
                blocked = True
        if blocked:
            fail('Overlapping branches require serialization')

    async def prepare_candidate(self, task, attempt_id, main):
        await self.ensure_ignored()
        branch = 'staging/integration/' + attempt_id
        path = self.path('integration-' + attempt_id)
        # Never reuse an uncertain candidate or reset an existing branch.
        await self.git('worktree', 'add', '-b', branch, str(path), main)
        await self.git('merge', '--no-ff', '--no-edit', task['review']['commit'], cwd=path)
        return branch, path, await self.head(branch)

    async def promote(self, task, candidate_branch, candidate, expected_main):
        if 'refs/heads/main' in await self.registrations():
            fail('Main must not be checked out during promotion')
        await self.git('merge-base', '--is-ancestor', expected_main, candidate)
        # A single Git ref transaction atomically verifies all three identities.
        await self.git('update-ref', '--stdin', stdin=(
            'start\n'
            f"verify refs/heads/{task['branch']} {task['review']['commit']}\n"
            f'verify refs/heads/{candidate_branch} {candidate}\n'
            f'update refs/heads/main {candidate} {expected_main}\n'
            'prepare\ncommit\n'))
