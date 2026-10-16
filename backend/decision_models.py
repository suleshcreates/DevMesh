from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Peer = Literal['codex', 'antigravity']

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class Risk(StrictModel):
    description: str = Field(min_length=1)
    blocking: bool

class Tradeoffs(StrictModel):
    security: str = Field(min_length=1)
    deployment: str = Field(min_length=1)
    complexity: str = Field(min_length=1)

class Critique(StrictModel):
    verdict: Literal['AGREE', 'CHALLENGE']
    identified_risks: list[Risk]
    counter_proposal: str | None
    tradeoff_analysis: Tradeoffs

    @model_validator(mode='after')
    def challenge_has_proposal(self):
        if self.verdict == 'CHALLENGE' and not (self.counter_proposal and self.counter_proposal.strip()):
            raise ValueError('CHALLENGE requires a counter proposal')
        return self

class Proposal(StrictModel):
    request_id: str = Field(pattern=r'^[a-zA-Z0-9-]{1,80}$')
    agent: Peer
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=100000)
    rationale: str = Field(min_length=1, max_length=100000)

class CritiqueRequest(StrictModel):
    request_id: str = Field(pattern=r'^[a-zA-Z0-9-]{1,80}$')
    agent: Peer
    expected_revision: int = Field(ge=1)
    critique_json: str = Field(max_length=100000)

class HumanResolution(StrictModel):
    request_id: str = Field(pattern=r'^[a-zA-Z0-9-]{1,80}$')
    expected_revision: int = Field(ge=1)
    directive: str = Field(min_length=1, max_length=100000)
    rationale: str = Field(min_length=1, max_length=100000)

class MemorySource(StrictModel):
    content: str = Field(max_length=100000)
