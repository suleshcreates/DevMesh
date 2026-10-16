"""Official FastMCP stdio bridge. Stdout belongs exclusively to MCP."""
from pathlib import Path
import sys
from uuid import UUID

# Direct execution by Antigravity does not put the repository root on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from mcp.server.fastmcp import FastMCP
from backend.models import Agent, MessageCreate, poll_timeout
from backend.security_guard import redact

class RedactingFastMCP(FastMCP):
    async def call_tool(self, name, arguments):
        try:
            return await super().call_tool(name, redact(arguments))
        except Exception:
            raise RuntimeError("DevMesh tool request failed") from None

mcp = RedactingFastMCP("devmesh-bridge", log_level="WARNING")
API = "http://127.0.0.1:8420"

async def request(method, path, *, payload=None, params=None, timeout=10):
    try:
        async with httpx.AsyncClient(base_url=API, timeout=timeout, trust_env=False) as client:
            response = await client.request(method, path, json=redact(payload), params=params)
            response.raise_for_status()
            return redact(response.json())
    except (httpx.HTTPError, ValueError):
        # Do not expose upstream response bodies or secret-bearing request values.
        raise RuntimeError("DevMesh API request failed") from None

@mcp.tool()
async def send_agent_message(sender: Agent, recipient: Agent, content: str,
                             subject: str = "", conversation_id: str | None = None,
                             requires_response: bool = False) -> dict:
    message = MessageCreate(sender=sender, recipient=recipient, content=content,
                            subject=subject, conversation_id=conversation_id,
                            requires_response=requires_response)
    return await request("POST", "/api/messages", payload=message.model_dump(mode="json"))

@mcp.tool()
async def read_agent_inbox(agent_name: Agent) -> dict:
    return await request("GET", f"/api/inbox/{agent_name}")

@mcp.tool()
async def await_next_task(agent_name: Agent, timeout_seconds: float = 45) -> dict:
    duration = poll_timeout(timeout_seconds)
    return await request("GET", f"/api/poll/{agent_name}",
                         params={"timeout_seconds": duration}, timeout=duration + 5)

@mcp.tool()
async def acknowledge_message(message_id: UUID, agent_name: Agent) -> dict:
    return await request("POST", f"/api/messages/{message_id}/ack", payload={"agent": agent_name})

@mcp.tool()
async def submit_proposal(sender: Agent, recipient: Agent, subject: str, content: str,
                          conversation_id: str | None = None) -> dict:
    message = MessageCreate(sender=sender, recipient=recipient, type="proposal",
                            subject=subject, content=content, conversation_id=conversation_id,
                            requires_response=True)
    return await request("POST", "/api/messages", payload=message.model_dump(mode="json"))

@mcp.tool()
async def submit_review(sender: Agent, recipient: Agent, conversation_id: str,
                        content: str, subject: str = "") -> dict:
    message = MessageCreate(sender=sender, recipient=recipient, type="review",
                            subject=subject, content=content, conversation_id=conversation_id)
    return await request("POST", "/api/messages", payload=message.model_dump(mode="json"))

@mcp.tool()
async def list_open_decisions() -> dict:
    return await request("GET", "/api/decisions")

@mcp.tool()
async def submit_decision_proposal(request_id: str, agent: str, title: str,
                                   content: str, rationale: str) -> dict:
    from backend.decision_models import Proposal
    body = Proposal(request_id=request_id, agent=agent, title=title, content=content, rationale=rationale)
    return await request("POST", "/api/decisions/propose", payload=body.model_dump())

@mcp.tool()
async def submit_critique(decision_id: str, request_id: str, agent: str,
                          expected_revision: int, critique_json: str) -> dict:
    import re
    from backend.decision_models import CritiqueRequest
    if not re.fullmatch(r"DEC-[0-9]+", decision_id):
        raise ValueError("Invalid decision ID")
    body = CritiqueRequest(request_id=request_id, agent=agent,
                          expected_revision=expected_revision, critique_json=critique_json)
    return await request("POST", f"/api/decisions/{decision_id}/critique", payload=body.model_dump())

@mcp.tool()
async def get_project_context(doc_name: str) -> dict:
    from backend.memory_manager import validate_name
    validate_name(doc_name)
    return await request("GET", f"/api/context/{doc_name}")

if __name__ == "__main__":
    mcp.run(transport="stdio")
