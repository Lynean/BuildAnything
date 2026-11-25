"""Model gateway: runs agents through the OpenAI Agents SDK and enforces the spending limit."""
from __future__ import annotations

import base64
import functools
import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, Sequence, TypeVar

from agents import WebSearchTool, Agent, AgentOutputSchema, FunctionTool, MaxTurnsExceeded, ModelSettings, OpenAIProvider, RunConfig, RunHooks, Runner, function_tool, set_tracing_export_api_key
from agents.exceptions import UserError
from openai.types.shared import Reasoning

from . import jobs
from .agent_specs import SPECS, render_input, system_prompt
from .ledger import Actor, Ledger
from .providers import KeyringStore, SecretStore, find_key

T = TypeVar("T")


class SpendLimitReached(Exception):
    pass


class MissingProviderKey(Exception):
    pass


@dataclass(frozen=True)
class Price:
    input: float
    cached: float
    cache_write: float
    output: float


# USD per 1M tokens. Read from OpenAI's pricing page on 2026-11-08.
PRICES: dict[str, Price] = {
    "gpt-6-luna": Price(0.10, 0.01, 0.125, 0.50),
    "gpt-6.1-terra": Price(1.00, 0.05, 1.25, 5.00),
    "gpt-6.1-sol": Price(2.00, 0.10, 2.50, 10.00),
}

OPENAI_TIERS = {"Fast": "gpt-6-luna", "Standard": "gpt-6.1-terra", "Deep": "gpt-6.1-sol"}
EFFORT = {"Fast": "low", "Standard": "medium", "Deep": "high"}
WEB_SEARCH_USD = 0.01  # $10 per 1,000 calls, from OpenAI's pricing page on 2026-11-09. Search content is billed as tokens.


@dataclass(frozen=True)
class PdfPage:
    """A PDF attachment, either in a request or returned by a tool."""

    data: bytes
    filename: str = "page.pdf"
    text: str = ""

    def data_url(self) -> str:
        return "data:application/pdf;base64," + base64.b64encode(self.data).decode("ascii")


@dataclass(frozen=True)
class Photo:
    """A picture a person attached to a message."""

    data_url: str


@dataclass(frozen=True)
class ToolDef:
    """A tool whose JSON schema is already known, such as one from an MCP server."""

    name: str
    description: str
    schema: dict[str, Any]
    function: Callable[..., Awaitable[Any]]


class ModelProvider(Protocol):
    def resolve(self, tier: str) -> tuple[Any, str, dict[str, Any]]:
        """Return the model, its price-table name, and run options for a tier."""


class OpenAIProviderAdapter:
    def __init__(self, store: SecretStore | None = None, tiers: dict[str, str] | None = None):
        self.store = store or KeyringStore()
        self.tiers = tiers or OPENAI_TIERS

    def resolve(self, tier: str) -> tuple[Any, str, dict[str, Any]]:
        key, _source = find_key(self.store, "OPENAI_API_KEY")
        if not key:
            raise MissingProviderKey("Add an OpenAI API key under Connectors.")
        set_tracing_export_api_key(key)
        name = self.tiers[tier]
        return name, name, {"effort": EFFORT[tier], "provider": OpenAIProvider(api_key=key, use_responses=True), "trace": True}


def cost_usd(price: Price, usage: Any) -> float:
    details = getattr(usage, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    written = getattr(details, "cache_write_tokens", 0) or 0
    plain = max(usage.input_tokens - cached - written, 0)
    return (plain * price.input + cached * price.cached + written * price.cache_write + usage.output_tokens * price.output) / 1e6


_IN_FLIGHT: dict[str, list["_Budget"]] = {}  # Runs side by side can't see each other's spending until they finish.


def _unrecorded(ledger: Ledger, leaving_out: Any = None) -> float:
    return sum(b.cost() for b in _IN_FLIGHT.get(str(ledger.root), []) if b is not leaving_out)


class _Budget(RunHooks):
    """Checks the money after every model call, so a long run can't overshoot by much."""

    def __init__(self, price: Price, ledger: Ledger, spec: Any = None, live: Any = None):
        self.price, self.ledger, self.spec, self.live = price, ledger, spec, live
        self.usage: Any = None
        self.searches = 0

    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        if self.live:
            jobs.note(self.live, "Thinking")

    async def on_tool_start(self, context, agent, tool) -> None:
        if self.live:
            jobs.note(self.live, _for_person(self.spec.tools.get(tool.name, tool.name.replace("_", " ").capitalize())))

    def cost(self) -> float:
        return (cost_usd(self.price, self.usage) if self.usage is not None else 0) + self.searches * WEB_SEARCH_USD

    async def on_llm_end(self, context, agent, response) -> None:
        self.usage = context.usage
        if self.live:
            _trail(self.live, self.spec, response)
        self.searches += sum(1 for item in response.output if getattr(item, "type", "") == "web_search_call")
        state = self.ledger.state()  # Re-read, because a request to another department spends too.
        if self.cost() + _unrecorded(self.ledger, self) > state.limit_usd - state.spent_usd:
            raise SpendLimitReached("This run stopped because it would exceed the project's limit.")


def _trail(live: Any, spec: Any, response: Any) -> None:
    """Keeps what the person may want to see of a model turn: its reasoning summary, the tools it called and with what, and what it said."""
    for item in getattr(response, "output", []) or []:
        kind = getattr(item, "type", "")
        if kind == "reasoning":
            for part in getattr(item, "summary", []) or []:
                jobs.step(live, "thought", getattr(part, "text", "") or "")
        elif kind == "function_call":
            name = getattr(item, "name", "")
            label = (spec.tools.get(name) if spec else None) or name.replace("_", " ").capitalize()
            try:
                args = json.loads(getattr(item, "arguments", "") or "{}")
                shown = ", ".join(f"{k}: {str(v)[:120]}" for k, v in args.items() if k not in ("content", "html", "script", "code"))[:300]
            except ValueError:
                shown = ""
            jobs.step(live, "tool", f"{_for_person(label).rstrip('.')}" + (f" ({shown})" if shown else ""))
        elif kind == "web_search_call":
            query = getattr(getattr(item, "action", None), "query", "") or ""
            jobs.step(live, "tool", f"Searched the web{': ' + query if query else ''}")
        elif kind == "message":
            text = " ".join(getattr(c, "text", "") for c in getattr(item, "content", []) or [])
            jobs.step(live, "said", text[:600])


def _tool_result(result: Any) -> Any:
    if isinstance(result, Photo):
        return [{"type": "image", "image_url": result.data_url, "detail": "high"}]
    if isinstance(result, list) and any(isinstance(item, Photo) for item in result):  # a picture a tool looked at, with its caption
        return [{"type": "image", "image_url": item.data_url, "detail": "high"} if isinstance(item, Photo) else {"type": "text", "text": str(item)} for item in result]
    if isinstance(result, PdfPage):
        return [{"type": "text", "text": result.text}, {"type": "file", "file_data": result.data_url(), "filename": result.filename}]
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False, default=str)
    return result


def _sdk_tool(tool: Any) -> Any:
    if isinstance(tool, WebSearchTool):
        return tool
    if isinstance(tool, ToolDef):
        async def invoke(_context: Any, arguments: str) -> Any:
            return _tool_result(await tool.function(**(json.loads(arguments) if arguments else {})))

        return FunctionTool(name=tool.name, description=tool.description, params_json_schema=tool.schema, on_invoke_tool=invoke, strict_json_schema=False)
    if inspect.iscoroutinefunction(tool):
        @functools.wraps(tool)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            return _tool_result(await tool(*args, **kwargs))
    else:
        @functools.wraps(tool)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            return _tool_result(tool(*args, **kwargs))
    return function_tool(wrapped)


def _output_schema(output_type: Any) -> Any:
    if output_type is str:
        return None
    try:
        return AgentOutputSchema(output_type, strict_json_schema=True)
    except UserError:
        return AgentOutputSchema(output_type, strict_json_schema=False)


class Gateway:
    def __init__(self, ledger: Ledger, provider: ModelProvider, prices: dict[str, Price] | None = None):
        self.ledger = ledger
        self.provider = provider
        self.prices = prices or PRICES

    async def run(self, *, agent: str, values: dict[str, Any], output_type: type[T] = str, tools: Sequence[Any] = (), request_limit: int = 8, attachments: Sequence[PdfPage] = (), first_tool: str | None = None, tier: str | None = None) -> T:  # type: ignore[assignment]
        spec = SPECS[agent]
        tier = tier or spec.tier  # a routine step can run a usually-Standard agent on the cheaper tier
        state = self.ledger.state()
        remaining = state.limit_usd - state.spent_usd - _unrecorded(self.ledger)
        if remaining <= 0:
            raise SpendLimitReached(f"This project reached its ${state.limit_usd:.2f} AI spending limit.")
        model, name, options = self.provider.resolve(tier)
        if name not in self.prices:
            raise RuntimeError(f"No price is set for {name}. Add one before using this model.")
        price = self.prices[name]

        settings: dict[str, Any] = {}
        if price.output > 0:
            settings["max_tokens"] = max(16, min(64000, int(remaining / price.output * 1e6)))
        if options.get("effort"):
            # Ask for a summary of the reasoning, so tool calls in the logs have an explanation.
            settings["reasoning"] = Reasoning(effort=options["effort"], summary="auto")
        if first_tool:
            settings["tool_choice"] = first_tool  # The SDK returns to free choice after this call.
        sdk_agent = Agent(name=spec.title, instructions=system_prompt(spec), model=model, tools=[_sdk_tool(t) for t in tools], output_type=_output_schema(output_type), model_settings=ModelSettings(**settings))

        text = render_input(spec, values)
        message: Any = text
        if attachments:
            message = [{"role": "user", "content": [{"type": "input_text", "text": text}, *[{"type": "input_image", "image_url": a.data_url, "detail": "high"} if isinstance(a, Photo) else {"type": "input_file", "file_data": a.data_url(), "filename": a.filename} for a in attachments]]}]

        extra = {"model_provider": options["provider"]} if options.get("provider") else {}
        config = RunConfig(
            **extra,
            workflow_name=f"{state.meta.get('name', 'Project')}: {spec.title}",
            group_id=str(self.ledger.root.name),
            trace_metadata={"agent": agent, "area": spec.area, "tier": tier},
            trace_include_sensitive_data=True,
            tracing_disabled=not options.get("trace"),
        )
        live = jobs.agent_began(self.ledger, agent, spec.area, spec.title)
        budget = _Budget(price, self.ledger, spec, live)
        running = _IN_FLIGHT.setdefault(str(self.ledger.root), [])
        running.append(budget)
        try:
            result = await Runner.run(sdk_agent, message, max_turns=request_limit, hooks=budget, run_config=config)
            return result.final_output
        except MaxTurnsExceeded as exc:
            raise SpendLimitReached("This run stopped after too many steps.") from exc
        finally:
            running.remove(budget)
            jobs.agent_ended(self.ledger, live)
            if budget.usage is not None and budget.usage.requests:
                self.ledger.append(
                    Actor.system("gateway"), "spend.recorded", agent=agent, area=spec.area, tier=tier, model=name,
                    usd=round(budget.cost(), 6), requests=budget.usage.requests, input=budget.usage.input_tokens, cached=getattr(getattr(budget.usage, "input_tokens_details", None), "cached_tokens", 0) or 0, output=budget.usage.output_tokens,
                )


def _for_person(text: str) -> str:
    """Tool descriptions speak to the agent ("your scripts"); the live line speaks about it ("its scripts")."""
    import re

    swaps = {"your": "its", "Your": "Its", "you": "it", "You": "It", "yourself": "itself"}
    return re.sub(r"\b(yourself|[Yy]our|[Yy]ou)\b", lambda m: swaps[m.group(1)], text)
