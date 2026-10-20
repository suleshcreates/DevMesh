"""Caller-driven peer review and tested, atomic ref promotion. No scheduler."""
import asyncio
import hashlib
import json
from backend.git_workspace import GitWorkspace, GitError, process
from backend.task_planner import fail, move, MAX_TOOL_CALLS_PER_TASK, TASK_TIMEOUT_SECONDS
from backend.security_guard import redact

class ReviewRunner:
    def __init__(self, planner, guard, repository, test_command=None):
        self.planner, self.guard = planner, guard
        self.workspace = GitWorkspace(repository)
        self.test_command = test_command
        self.lock = asyncio.Lock()

    async def recover(self):
        # No blind replay of subprocess effects after a crash.
        async with self.planner.db.lock:
            c = self.planner.db.connection
            rows = await (await c.execute("SELECT * FROM integration_attempts WHERE state='running'")).fetchall()
            try:
                for row in rows:
                    task = await self.planner._get(c, row['task_id'])
                    if task['status'] != 'blocked':
                        move(task, 'blocked')
                    task['revision'] += 1
                    task['review_feedback'].append('Interrupted integration; operator must inspect candidate and main. No automatic replay.')
                    await self.planner.save(c, task)
                    await self.planner.message(c, 'human', 'HUMAN_APPROVAL_REQUIRED', {'task_id': task['id'], 'attempt': row['id'], 'reason': 'Interrupted integration'})
                    await c.execute("UPDATE integration_attempts SET state='interrupted' WHERE id=?", (row['id'],))
                await c.commit()
            except BaseException:
                await c.rollback()
                raise

    async def refresh(self, task):
        if task['status'] in ('review', 'testing') and task['review']:
            if await self.workspace.head(task['branch']) != task['review']['commit']:
                async def invalidate(c):
                    t = await self.planner._get(c, task['id'])
                    t['review'] = None
                    move(t, 'in_progress')
                    t['revision'] += 1
                    await self.planner.save(c, t)
                    return t
                return await self.planner.operation('invalidate-' + task['id'] + '-' + str(task['revision']),
                                                     ['invalidate', task['id'], task['revision']], invalidate)
        return task

    async def workspace_for(self, task_id, request_id):
        task = await self.planner.get(task_id)
        if task['status'] not in ('assigned', 'in_progress'):
            fail('Assign task before creating workspace')
        async def save(c):
            t = await self.planner._get(c, task_id)
            await self.planner.dependencies_ready(c, t)
            branch, path = await self.workspace.create(t)
            t.update(branch=branch, worktree_path=path, revision=t['revision'] + 1)
            await self.planner.save(c, t)
            return t
        return await self.planner.operation(request_id, ['workspace', task_id], save)

    async def review_ready(self, task_id, request_id):
        task = await self.refresh(await self.planner.get(task_id))
        if task['status'] == 'review' and task['review']:
            return task
        if task['status'] != 'in_progress':
            fail('Only in-progress tasks can request peer review')
        await self.workspace.check_scope(task)
        commit = await self.workspace.head(task['branch'])
        # Pin both diff ends. Never invoke external diff drivers/textconv.
        base = await self.workspace.git('merge-base', 'main', commit)
        diff = redact(await self.workspace.git('diff', '--no-ext-diff', '--no-textconv', base, commit, '--'))
        if len(diff) > 80000:
            fail('Diff exceeds review message limit; split the task')
        async def save(c):
            t = await self.planner._get(c, task_id)
            await self.planner.dependencies_ready(c, t)
            if t['tool_calls'] > MAX_TOOL_CALLS_PER_TASK:
                move(t, 'blocked')
                await self.planner.message(c, 'human', 'HUMAN_APPROVAL_REQUIRED', {'task_id': task_id, 'reason': 'MAX_TOOL_CALLS_PER_TASK'})
            else:
                move(t, 'review')
                reviewer = 'antigravity' if t['assigned_agent'] == 'codex' else 'codex'
                t['review'] = dict(commit=commit, base=base, reviewer=reviewer, verdict=None, diff=diff)
                await self.planner.message(c, reviewer, 'code_review',
                         {'task_id': task_id, 'commit': commit, 'base': base, 'diff': diff,
                          'acceptance_criteria': t['acceptance_criteria'],
                          'required_verdict': ['APPROVED', 'CHANGES_REQUESTED']},
                         t['assigned_agent'], 'code_review')
            t['revision'] += 1
            await self.planner.save(c, t)
            return t
        return await self.planner.operation(request_id, ['review-ready', task_id, commit], save)

    async def feedback(self, task_id, request):
        task = await self.refresh(await self.planner.get(task_id))
        async def save(c):
            t = await self.planner._get(c, task_id)
            review = t['review']
            if not review or t['status'] != 'review':
                fail('Task has no current review')
            if request.agent == t['assigned_agent'] or request.agent != review['reviewer']:
                fail('Review must come from the other assigned peer')
            if request.commit != review['commit']:
                fail('Review commit does not match')
            if review['verdict']:
                fail('Review attempt already decided')
            t['review_feedback'].append(redact(request.model_dump()))
            if request.verdict == 'CHANGES_REQUESTED':
                await self.planner.failure(c, t, request.feedback)
            else:
                review['verdict'] = 'APPROVED'
                t['revision'] += 1
                await self.planner.save(c, t)
            return t
        return await self.planner.operation(request.request_id, ['feedback', task_id, request.model_dump()], save)

    def action(self, task, request_id):
        if not self.test_command or not isinstance(self.test_command, list) or not all(isinstance(s, str) and s for s in self.test_command):
            fail('Operator must configure a test command argument array')
        attempt = task['id'] + '-' + hashlib.sha256(request_id.encode()).hexdigest()[:20]
        path = self.workspace.path('integration-' + attempt)
        return attempt, dict(kind='command', arguments=self.test_command,
                repository=str(self.workspace.root), worktree=str(path),
                scope=task['touched_files_scope'], target=None,
                context={'task_id': task['id'], 'reviewed_commit': task['review']['commit'] if task['review'] else 'unreviewed'})

    async def integration_action(self, task_id, request_id):
        task = await self.planner.get(task_id)
        attempt, action = self.action(task, request_id)
        return {'attempt_id': attempt, 'action': action}

    async def integrate(self, task_id, request_id, approval_id):
        task = await self.planner.get(task_id)
        attempt, action = self.action(task, request_id)
        async with self.planner.db.lock:
            prior = await (await self.planner.db.connection.execute('SELECT * FROM integration_attempts WHERE id=?', (attempt,))).fetchone()
        if prior:
            if prior['state'] == 'finished':
                return json.loads(prior['document'])
            fail('Integration attempt is uncertain; operator inspection required')
        task = await self.refresh(task)
        if task['status'] != 'review' or not task['review'] or task['review']['verdict'] != 'APPROVED':
            fail('Current exact-commit peer approval required')
        async with self.planner.db.lock:
            await self.planner.dependencies_ready(self.planner.db.connection, task)
        await self.workspace.overlap(task, self.planner)
        await self.workspace.check_scope(task)
        if 'refs/heads/main' in await self.workspace.registrations():
            fail('Main must not be checked out during promotion')
        main = await self.workspace.head('main')

        async def execute():
            async with self.planner.db.lock:
                c = self.planner.db.connection
                try:
                    await c.execute('BEGIN IMMEDIATE')
                    current = await self.planner._get(c, task_id)
                    if current['revision'] != task['revision'] or current['status'] != 'review':
                        fail('Task changed before integration reservation')
                    if current['tool_calls'] >= MAX_TOOL_CALLS_PER_TASK:
                        move(current, 'blocked')
                        current['review'] = None
                        current['revision'] += 1
                        await self.planner.save(c, current)
                        await self.planner.message(c, 'human', 'HUMAN_APPROVAL_REQUIRED', {'task_id': task_id, 'reason': 'MAX_TOOL_CALLS_PER_TASK'})
                        await c.commit()
                        return {'task': current, 'promoted': False, 'reason': 'MAX_TOOL_CALLS_PER_TASK'}
                    task['tool_calls'] += 1
                    move(task, 'testing')
                    task['revision'] += 1
                    await self.planner.save(c, task)
                    await c.execute('INSERT INTO integration_attempts VALUES (?,?,?,?)',
                                    (attempt, task_id, 'running', json.dumps({'main_before': main, 'reviewed_commit': task['review']['commit']})))
                    await c.commit()
                except BaseException:
                    await c.rollback()
                    raise
            promoted = False
            candidate = None
            try:
                async with asyncio.timeout(TASK_TIMEOUT_SECONDS):
                    branch, path, candidate = await self.workspace.prepare_candidate(task, attempt, main)
                    async with self.planner.db.lock:
                        await c.execute('UPDATE integration_attempts SET document=? WHERE id=?',
                                        (json.dumps({'main_before': main, 'candidate': candidate, 'branch': branch, 'worktree': str(path)}), attempt))
                        await c.commit()
                    code, out, err = await process(self.test_command, path, timeout=TASK_TIMEOUT_SECONDS)
                    logs = redact(out + '\n' + err)[-40000:]
                    if code != 0:
                        raise GitError('Configured tests failed: ' + logs)
                    if await self.workspace.git('status', '--porcelain', '--untracked-files=all', cwd=path):
                        raise GitError('Tests modified the integration worktree')
                    if await self.workspace.git('rev-parse', 'HEAD', cwd=path) != candidate:
                        raise GitError('Candidate changed during tests')
                    await self.workspace.check_scope(task)
                    await self.workspace.promote(task, branch, candidate, main)
                    promoted = True
            except Exception as exc:
                # Preserve redacted expected diagnostics only, never arbitrary exception payloads.
                from backend.decision_engine import DecisionError
                logs = redact(str(exc)) if isinstance(exc, (GitError, DecisionError)) else type(exc).__name__
                async with self.planner.db.lock:
                    await self.planner.failure(c, task, {'attempt': attempt, 'test_log': logs, 'candidate': candidate})
                    result = {'task': task, 'promoted': False, 'candidate': candidate, 'test_log': logs}
                    await c.execute("UPDATE integration_attempts SET state='finished',document=? WHERE id=?", (json.dumps(redact(result)), attempt))
                    await c.commit()
                return result
            if promoted:
                async with self.planner.db.lock:
                    move(task, 'complete')
                    task['revision'] += 1
                    await self.planner.save(c, task)
                    result = redact({'task': task, 'promoted': True, 'candidate': candidate, 'test_log': logs})
                    await c.execute("UPDATE integration_attempts SET state='finished',document=? WHERE id=?", (json.dumps(result), attempt))
                    await c.commit()
                return result
        return await self.guard.execute(approval_id, action, execute)
