"""Give an agent connected tools, within the autonomy level you set for each capability."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from . import connectors as conn
from .gateway import ToolDef
from .ledger import Actor

ToolCaller = Callable[[dict[str, Any], str, dict[str, Any]], Awaitable[Any]]


async def call_mcp_tool(spec: dict[str, Any], name: str, arguments: dict[str, Any]) -> Any:
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    source: Any = spec["url"] if "url" in spec else StdioTransport(command=spec["command"], args=spec["args"], env=conn.clean_environment())
    async with Client(source) as client:
        result = await client.call_tool(name, arguments)
    return result.data if getattr(result, "data", None) is not None else [getattr(c, "text", str(c)) for c in result.content]


def allowed_mode(level: str, risk: str) -> str:
    """Returns run, approval or hidden. Risky actions can never run without approval."""
    if level == "Observe":
        return "hidden"
    if risk == "read":
        return "run"
    if level == "Suggest":
        return "hidden"
    if level == "Act" and risk not in conn.CAPPED_RISKS:
        return "run"
    return "approval"


def build_tools(registry: conn.Registry, capabilities: list[str], agent: str, caller: ToolCaller = call_mcp_tool) -> list[ToolDef]:
    """Only mapped, healthy, approved tools are offered. Call session_tools first to re-check servers."""
    levels = {c["id"]: c["autonomy"] for c in registry.overview()}
    tools: list[ToolDef] = []
    for connector in registry.connectors():
        if connector["rung"] != "mcp" or connector["health"] != "passed":
            continue
        for item in connector.get("tools", []):
            capability = item.get("capability")
            if capability not in capabilities:
                continue
            mode = allowed_mode(levels[capability], item["risk"])
            if mode == "hidden":
                continue
            tools.append(_wrap(registry, connector, item, mode, agent, caller))
    return tools


def _wrap(registry: conn.Registry, connector: dict[str, Any], item: dict[str, Any], mode: str, agent: str, caller: ToolCaller) -> ToolDef:
    async def invoke(**arguments: Any) -> Any:
        if mode == "approval":
            registry.ledger.append(Actor.system("agent-tools"), "tool.approval_requested", agent=agent, connector=connector["id"], tool=item["name"], arguments=arguments)
            return {"status": "waiting_for_approval", "message": "A person must approve this action. It has not run."}
        return await caller(connector["spec"], item["name"], arguments)

    return ToolDef(name=item["name"], description=item.get("description") or "", schema=item.get("schema") or {"type": "object", "properties": {}}, function=invoke)
