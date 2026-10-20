"""Strict local task/operator request contracts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Agent = Literal['codex', 'antigravity']
Status = Literal['planned', 'assigned', 'in_progress', 'review', 'testing', 'complete', 'blocked']
class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')
class Request(Body):
    request_id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9-]+$')
class TaskCreate(Request):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=20000)
    category: Literal['backend', 'database', 'logic', 'debugging', 'unit_tests', 'ui_ux', 'frontend', 'browser_qa'] = 'backend'
    assigned_agent: Agent | None = None
    dependencies: list[str] = Field(default_factory=list, max_length=100)
    touched_files_scope: list[str] = Field(min_length=1, max_length=100)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=100)
class TaskStatus(Request):
    agent: Agent
    expected_revision: int = Field(ge=1)
    status: Status
class ReviewFeedback(Request):
    agent: Agent
    commit: str = Field(pattern=r'^[a-f0-9]{40,64}$')
    verdict: Literal['APPROVED', 'CHANGES_REQUESTED']
    feedback: str = Field(min_length=1, max_length=20000)
class Challenge(Request):
    agent: Agent
    content: str = Field(min_length=1, max_length=20000)
class Action(Body):
    kind: Literal['command', 'write', 'deploy']
    arguments: list[str] = Field(min_length=1, max_length=100)
    repository: str
    worktree: str
    scope: list[str] = Field(min_length=1, max_length=100)
    target: str | None = None
    context: dict[str, str] = Field(default_factory=dict, max_length=20)
class ApprovalDecision(Body):
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    decision: Literal['approved', 'denied']
    reason: str = Field(min_length=1, max_length=2000)
class Integration(Request):
    approval_id: str
