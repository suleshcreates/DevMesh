import math
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

Agent = Literal["codex", "antigravity", "human"]

class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID | None = None
    sender: Agent
    recipient: Agent
    type: Literal["message", "proposal", "review"] = "message"
    subject: str = Field(default="", max_length=500)
    content: str = Field(min_length=1, max_length=100000)
    requires_response: bool = False

class Acknowledgement(BaseModel):
    agent: Agent

def poll_timeout(value: float) -> float:
    if not math.isfinite(value) or value < 0:
        raise ValueError("Invalid timeout")
    return min(value, 45.0)
