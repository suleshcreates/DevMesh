from typing import Literal
from pydantic import Field
from backend.decision_models import StrictModel
from backend.task_models import TaskCreate

class StartProject(StrictModel):
    request_id: str = Field(pattern=r'^[A-Za-z0-9-]{1,80}$')
    goal: str = Field(min_length=1, max_length=20000)
    repository: str = Field(min_length=1)
    test_command: list[str] = Field(min_length=1, max_length=100)
    execution_mode: Literal['manual'] = 'manual'

class ProjectControl(StrictModel):
    request_id: str = Field(pattern=r'^[A-Za-z0-9-]{1,80}$')
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    manual_handoff: bool = False

class AgentReport(StrictModel):
    request_id: str = Field(pattern=r'^[A-Za-z0-9-]{1,80}$')
    correlation_id: str
    agent: Literal['codex', 'antigravity']
    phase: Literal['analysis', 'critique', 'plan', 'execution', 'review']
    task_id: str | None = None
    decision_id: str | None = None
    commit: str | None = None
    payload: dict

class Analysis(StrictModel):
    content: str = Field(min_length=1, max_length=30000)
    rationale: str = Field(min_length=1, max_length=10000)

class PlanItem(TaskCreate):
    key: str = Field(pattern=r'^[A-Za-z0-9-]{1,40}$')

class Plan(StrictModel):
    tasks: list[PlanItem] = Field(min_length=1, max_length=30)

class ExecutionResult(StrictModel):
    commit: str = Field(pattern=r'^[a-f0-9]{40,64}$')
    summary: str = Field(min_length=1, max_length=10000)

class ReviewResult(StrictModel):
    verdict: Literal['APPROVED', 'CHANGES_REQUESTED']
    feedback: str = Field(min_length=1, max_length=20000)

class Usage(StrictModel):
    agent: Literal['codex', 'antigravity']
    correlation_id: str
    event_id: str = Field(min_length=1, max_length=200)
    kind: Literal['tool', 'progress', 'error', 'stall']
    detail: str = Field(max_length=2000, default='')
