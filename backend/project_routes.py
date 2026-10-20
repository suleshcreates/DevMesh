"""Local operator lifecycle controls; agent results reuse existing domain validation."""
from fastapi import Query
from backend.project_models import StartProject, ProjectControl, AgentReport, Usage

def register(app):
    @app.get('/api/projects')
    async def projects():
        return {'projects': await app.state.orchestrator.list()}

    @app.post('/api/projects/start', status_code=201)
    async def start(request: StartProject):
        return await app.state.orchestrator.start(request)

    @app.get('/api/projects/{project_id}')
    async def project(project_id: str):
        return await app.state.orchestrator.get(project_id)

    @app.get('/api/projects/{project_id}/activation')
    async def activation(project_id: str):
        p = await app.state.orchestrator.get(project_id)
        return {'project_id': project_id, 'prompt': app.state.orchestrator.activation(p)}

    @app.get('/api/projects/{project_id}/assignments/{correlation_id}')
    async def context(project_id: str, correlation_id: str, agent: str = Query(pattern='^(codex|antigravity)$'), request_id: str | None = None):
        from uuid import uuid4
        job = await app.state.orchestrator.assigned_context(project_id, correlation_id, agent)
        if job['task_id']:
            await app.state.orchestrator.usage(project_id, Usage(agent=agent, correlation_id=correlation_id, event_id='context-' + (request_id or str(uuid4())), kind='tool'))
        return await app.state.orchestrator.assigned_context(project_id, correlation_id, agent)

    @app.post('/api/projects/{project_id}/reports')
    async def report(project_id: str, request: AgentReport):
        return await app.state.orchestrator.report(project_id, request)

    @app.post('/api/projects/{project_id}/usage')
    async def usage(project_id: str, request: Usage):
        return await app.state.orchestrator.usage(project_id, request)

    @app.post('/api/projects/{project_id}/pause')
    async def pause(project_id: str, request: ProjectControl):
        return await app.state.orchestrator.control(project_id, 'pause', request)

    @app.post('/api/projects/{project_id}/resume')
    async def resume(project_id: str, request: ProjectControl):
        return await app.state.orchestrator.control(project_id, 'resume', request)

    @app.post('/api/projects/{project_id}/cancel')
    async def cancel(project_id: str, request: ProjectControl):
        return await app.state.orchestrator.control(project_id, 'cancel', request)
