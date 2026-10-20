"""Local-only task and operator routes. Agent labels are not authenticated."""
from backend.task_models import TaskCreate, TaskStatus, Request, ReviewFeedback, Challenge, Action, ApprovalDecision, Integration
from backend.git_workspace import GitError
from fastapi.responses import JSONResponse

def register(app):
    @app.exception_handler(GitError)
    async def git_error(request, exc):
        return JSONResponse(status_code=409, content={'detail': str(exc)})

    async def mutate(callback):
        async with app.state.runner.lock:
            try:
                return await callback()
            finally:
                # Overlap/recovery can commit a blocked task before rejecting a request.
                await app.state.memory.context('TASKS.md')
                async with app.state.changed:
                    app.state.changed.notify_all()

    @app.post('/api/tasks', status_code=201)
    async def create(request: TaskCreate):
        return await mutate(lambda: app.state.tasks.create(request))

    @app.get('/api/tasks')
    async def tasks():
        async def fetch():
            return {'tasks': [await app.state.runner.refresh(t) for t in await app.state.tasks.list()]}
        return await mutate(fetch)

    @app.get('/api/tasks/{task_id}')
    async def task(task_id: str):
        return await mutate(lambda: get_task(task_id))

    async def get_task(task_id):
        return await app.state.runner.refresh(await app.state.tasks.get(task_id))

    @app.post('/api/tasks/{task_id}/status')
    async def status(task_id: str, request: TaskStatus):
        return await mutate(lambda: app.state.tasks.update(task_id, request))

    @app.post('/api/tasks/{task_id}/workspace')
    async def workspace(task_id: str, request: Request):
        return await mutate(lambda: app.state.runner.workspace_for(task_id, request.request_id))

    @app.post('/api/tasks/{task_id}/review')
    async def review(task_id: str, request: Request):
        return await mutate(lambda: app.state.runner.review_ready(task_id, request.request_id))

    @app.post('/api/tasks/{task_id}/feedback')
    async def feedback(task_id: str, request: ReviewFeedback):
        return await mutate(lambda: app.state.runner.feedback(task_id, request))

    @app.post('/api/tasks/{task_id}/api-contract-challenge')
    async def challenge(task_id: str, request: Challenge):
        return await mutate(lambda: app.state.tasks.challenge(task_id, request))

    @app.post('/api/tasks/{task_id}/integration-action')
    async def integration_action(task_id: str, request: Request):
        return await app.state.runner.integration_action(task_id, request.request_id)

    @app.post('/api/tasks/{task_id}/integrate')
    async def integrate(task_id: str, request: Integration):
        return await mutate(lambda: app.state.runner.integrate(task_id, request.request_id, request.approval_id))

    @app.post('/api/approvals', status_code=201)
    async def approval(request: Action):
        return await app.state.guard.request(request.model_dump())

    @app.get('/api/approvals')
    async def approvals():
        return {'requests': await app.state.guard.list()}

    @app.post('/api/approvals/{approval_id}/decision')
    async def decide(approval_id: str, request: ApprovalDecision):
        return await app.state.guard.decide(approval_id, request)

    @app.get('/api/audit-logs')
    async def audit_logs(after_id: int = 0):
        import json
        async with app.state.db.lock:
            rows = await (await app.state.db.connection.execute(
                'SELECT * FROM audit_logs WHERE id>? ORDER BY id LIMIT 100', (after_id,))).fetchall()
        return {'audit_logs': [{**dict(r), 'document': json.loads(r['document'])} for r in rows]}
