"""Connections to outside services: online APIs and MCP servers, used by the agents and shown in tool tabs.

External Service researches a service and proposes a connection: where it is, how to sign in, the setup steps for
the person, and a list of named operations, each marked as reading or changing something. A person approves it,
then types the key (or signs in) straight into the keyring. From then on agents call the operations by name; the
app's server makes the request and adds the credentials itself, so no agent, script or tab ever sees a key, and a
request can only go to the approved address, for an approved operation. Operations that change something wait for
the person every time.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets as pysecrets
import time
import urllib.parse
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .ledger import Actor, Ledger, LedgerError

CONN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
MAX_RESULT = 20_000
CALLS_PER_MINUTE = 60
TIMEOUT = 30.0
REDIRECT_PATH = "/api/connections/oauth/callback"


class CloudError(LedgerError):
    pass


class KeyField(BaseModel):
    id: str
    label: str
    where: str = ""  # where the person gets it, such as "nexar.com, Your apps, Client ID"
    secret: bool = True

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not NAME.fullmatch(v):
            raise ValueError("key field ids are lowercase_words")
        return v


class OAuth(BaseModel):
    grant: Literal["authorization_code", "client_credentials"]
    token_url: str
    authorize_url: str = ""
    scopes: list[str] = []
    client_id_field: str = "client_id"
    client_secret_field: str = ""  # empty for public clients using PKCE
    extra_authorize_params: dict[str, str] = {}


class Auth(BaseModel):
    type: Literal["none", "header", "bearer", "query", "oauth2", "env"]
    field: str = ""  # the key field holding the token, for header, bearer and query
    header_name: str = "Authorization"
    query_name: str = ""
    oauth: OAuth | None = None


class Param(BaseModel):
    name: str
    type: Literal["string", "number", "integer", "boolean", "object", "array"] = "string"
    description: str = ""
    required: bool = True


class Operation(BaseModel):
    name: str
    description: str
    writes: bool
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "MCP"] = "GET"
    path: str = ""
    query: dict[str, str] = {}
    body: Any = None
    params: list[Param] = []
    mcp_tool: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME.fullmatch(v):
            raise ValueError("operation names are lowercase_words, such as list_projects")
        return v


class Manifest(BaseModel):
    id: str
    name: str
    summary: str
    kind: Literal["api", "mcp"]
    base_url: str = ""
    mcp_target: str = ""
    fields: list[KeyField] = []
    auth: Auth
    setup: list[str] = Field(default_factory=list)
    departments: list[str]
    operations: list[Operation]
    check: str  # a read operation that proves the connection works
    sources: list[str] = []


def _host_ok(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return (parts.scheme == "https" and bool(parts.hostname)) or (parts.scheme == "http" and parts.hostname in LOOPBACK)


def validate(raw: dict[str, Any]) -> Manifest:
    try:
        m = Manifest(**raw)
    except Exception as exc:  # pydantic's message, plainly
        raise CloudError(f"The connection description isn't valid: {exc}") from exc
    if not CONN_ID.fullmatch(m.id):
        raise CloudError("Connection ids are short, lowercase, with hyphens, such as altium-365.")
    if m.kind == "api":
        if not _host_ok(m.base_url):
            raise CloudError("base_url must be https (or http on this computer only, 127.0.0.1).")
        for op in m.operations:
            if op.method == "MCP":
                raise CloudError(f"{op.name}: an API operation needs an HTTP method.")
            if not op.path.startswith("/") or "://" in op.path or ".." in op.path:
                raise CloudError(f"{op.name}: path is relative to base_url and starts with /, such as /v1/projects/{{id}}.")
            if op.method in ("PUT", "PATCH", "DELETE") and not op.writes:
                raise CloudError(f"{op.name}: {op.method} changes something, so writes must be true.")
            if re.search(r"\bmutation\b", json.dumps(op.body or "")) and not op.writes:
                raise CloudError(f"{op.name}: a GraphQL mutation changes something, so writes must be true.")
    else:
        target = m.mcp_target.strip()
        if not target:
            raise CloudError("An MCP connection needs mcp_target: the server's URL or the command that starts it.")
        if re.match(r"https?://", target, re.I) and not _host_ok(target):
            raise CloudError("An MCP URL must be https, or http on this computer (127.0.0.1).")
        for op in m.operations:
            if not op.mcp_tool:
                raise CloudError(f"{op.name}: name the MCP tool it calls in mcp_tool.")
            op.method = "MCP"
    names = [op.name for op in m.operations]
    if len(names) != len(set(names)) or not names:
        raise CloudError("List at least one operation, each with its own name.")
    check = next((op for op in m.operations if op.name == m.check), None)
    if not check or check.writes:
        raise CloudError("check must name one of the operations that only reads.")
    field_ids = {f.id for f in m.fields}
    if m.auth.type in ("header", "bearer", "query") and m.auth.field not in field_ids:
        raise CloudError(f"auth.field must be one of the key fields: {', '.join(field_ids) or 'none listed'}.")
    if m.auth.type == "query" and not m.auth.query_name:
        raise CloudError("A query key needs auth.query_name, the URL parameter it goes in.")
    if m.auth.type == "oauth2":
        o = m.auth.oauth
        if not o or not _host_ok(o.token_url) or (o.grant == "authorization_code" and not _host_ok(o.authorize_url)):
            raise CloudError("oauth2 needs https token_url (and authorize_url for sign-in).")
        if o.client_id_field not in field_ids or (o.client_secret_field and o.client_secret_field not in field_ids):
            raise CloudError("The OAuth client id (and secret, if any) must be key fields the person fills in.")
    from .tabs import DEPARTMENTS

    bad = [d for d in m.departments if d not in DEPARTMENTS]
    if bad or not m.departments:
        raise CloudError(f"departments are some of {', '.join(DEPARTMENTS)}.")
    for op in m.operations:
        declared = {p.name for p in op.params}
        used = set(PLACEHOLDER.findall(op.path + json.dumps(op.query) + json.dumps(op.body if op.body is not None else "")))
        if used - declared:
            raise CloudError(f"{op.name}: {', '.join(sorted(used - declared))} used but not listed in params.")
    return m


# ---- storage: proposed and approved copies, like Skills and tabs ----

STORES: dict[str, "Store"] = {}


def _empty(data: Any) -> bool:
    """Nothing came back: no data, or only blank text."""
    if data is None:
        return True
    if isinstance(data, str):
        return not data.strip() or data.strip().lower() in ("null", "none", "{}", "[]", "ok", "success")
    if isinstance(data, (list, tuple)):
        return all(_empty(x) for x in data)
    if isinstance(data, dict):
        return not data or all(_empty(v) for v in data.values())
    return False


def store_for(app_root: Path) -> "Store | None":
    return STORES.get(str(Path(app_root).resolve()))


class Store:
    def __init__(self, app_ledger: Ledger, secret_store: Any):
        STORES[str(app_ledger.root.resolve())] = self
        self.ledger = app_ledger
        self.secrets = secret_store
        self.root = app_ledger.root / "connections"
        self._calls: dict[str, list[float]] = {}
        self._pending_oauth: dict[str, dict[str, str]] = {}

    def _dir(self, conn_id: str) -> Path:
        if not CONN_ID.fullmatch(conn_id):
            raise CloudError("Unknown connection.")
        return self.root / conn_id

    def _read(self, conn_id: str, which: str) -> dict[str, Any] | None:
        path = self._dir(conn_id) / f"{which}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def _write(self, conn_id: str, which: str, data: dict[str, Any]) -> None:
        path = self._dir(conn_id) / f"{which}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")

    @staticmethod
    def _hash(data: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    def propose(self, raw: dict[str, Any], why: str, actor: Actor) -> dict[str, Any]:
        m = validate(raw)
        data = m.model_dump()
        self._write(m.id, "proposed", data)
        self.ledger.append(actor, "conn.proposed", id=m.id, name=m.name, hash=self._hash(data), why=why.strip()[:600],
                           operations=[op.name for op in m.operations], writes=[op.name for op in m.operations if op.writes])
        return data

    def decide(self, conn_id: str, approve: bool, reason: str, actor: Actor) -> None:
        proposed = self._read(conn_id, "proposed")
        if not proposed:
            raise CloudError("Nothing is waiting for that connection.")
        self.ledger.append(actor, "conn.decided", id=conn_id, approved=approve, reason=reason.strip()[:600], hash=self._hash(proposed))
        if approve:
            self._write(conn_id, "approved", proposed)
        (self._dir(conn_id) / "proposed.json").unlink()

    def remove(self, conn_id: str, actor: Actor) -> None:
        approved = self.approved(conn_id)
        self.ledger.append(actor, "conn.removed", id=conn_id)  # the person-only check comes first
        if approved:
            for f in approved.fields:
                self.secrets.delete(self._secret_name(conn_id, f.id))
            self.secrets.delete(self._secret_name(conn_id, "oauth"))
        for name in ("approved", "proposed"):
            (self._dir(conn_id) / f"{name}.json").unlink(missing_ok=True)

    def approved(self, conn_id: str) -> Manifest | None:
        data = self._read(conn_id, "approved")
        return Manifest(**data) if data else None

    # ---- keys and sign-in ----

    @staticmethod
    def _secret_name(conn_id: str, field: str) -> str:
        return f"conn.{conn_id}.{field}"

    def set_key(self, conn_id: str, field: str, value: str, actor: Actor) -> None:
        m = self.approved(conn_id)
        if not m or field not in {f.id for f in m.fields}:
            raise CloudError("Approve the connection first, then fill in its own key fields.")
        if not value.strip():
            raise CloudError("Paste the value first.")
        self.ledger.append(actor, "conn.key_set", id=conn_id, field=field)  # the person-only check comes first; never the value
        self.secrets.set(self._secret_name(conn_id, field), value.strip())

    def _key(self, conn_id: str, field: str) -> str | None:
        return self.secrets.get(self._secret_name(conn_id, field))

    def missing_keys(self, m: Manifest) -> list[str]:
        return [f.label for f in m.fields if not self._key(m.id, f.id)]

    def _oauth_tokens(self, conn_id: str) -> dict[str, Any] | None:
        raw = self._key(conn_id, "oauth")
        return json.loads(raw) if raw else None

    def oauth_start(self, conn_id: str, redirect_uri: str) -> str:
        m = self.approved(conn_id)
        if not m or m.auth.type != "oauth2" or not m.auth.oauth or m.auth.oauth.grant != "authorization_code":
            raise CloudError("That connection doesn't sign in through a browser.")
        o = m.auth.oauth
        client_id = self._key(conn_id, o.client_id_field)
        if not client_id:
            raise CloudError("Fill in the client ID first.")
        state, verifier = pysecrets.token_urlsafe(24), pysecrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self._pending_oauth[state] = {"id": conn_id, "verifier": verifier, "redirect_uri": redirect_uri, "at": str(time.time())}
        params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": " ".join(o.scopes), "state": state,
                  "code_challenge": challenge, "code_challenge_method": "S256", **o.extra_authorize_params}
        return o.authorize_url + ("&" if "?" in o.authorize_url else "?") + urllib.parse.urlencode(params)

    async def oauth_finish(self, state: str, code: str) -> str:
        pending = self._pending_oauth.pop(state, None)
        if not pending or time.time() - float(pending["at"]) > 900:
            raise CloudError("That sign-in link expired or wasn't started here. Start it again from External Service.")
        m = self.approved(pending["id"])
        o = m.auth.oauth  # type: ignore[union-attr]
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": pending["redirect_uri"], "client_id": self._key(m.id, o.client_id_field), "code_verifier": pending["verifier"]}
        if o.client_secret_field:
            form["client_secret"] = self._key(m.id, o.client_secret_field)
        tokens = await self._token_request(o.token_url, form)
        self._save_tokens(m.id, tokens)
        self.ledger.append(Actor.system("connections"), "conn.signed_in", id=m.id)
        return m.name

    async def _token_request(self, url: str, form: dict[str, Any]) -> dict[str, Any]:
        import httpx

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(url, data={k: v for k, v in form.items() if v})
        if r.status_code >= 400:
            raise CloudError(f"The service refused the sign-in ({r.status_code}): {r.text[:300]}")
        return r.json()

    def _save_tokens(self, conn_id: str, tokens: dict[str, Any]) -> None:
        old = self._oauth_tokens(conn_id) or {}
        keep = {"access_token": tokens["access_token"], "refresh_token": tokens.get("refresh_token") or old.get("refresh_token"),
                "expires_at": time.time() + float(tokens.get("expires_in") or 3600) - 60}
        self.secrets.set(self._secret_name(conn_id, "oauth"), json.dumps(keep))

    async def _access_token(self, m: Manifest) -> str:
        o = m.auth.oauth
        assert o is not None
        tokens = self._oauth_tokens(m.id)
        if tokens and tokens["expires_at"] > time.time():
            return tokens["access_token"]
        client = {"client_id": self._key(m.id, o.client_id_field)}
        if o.client_secret_field:
            client["client_secret"] = self._key(m.id, o.client_secret_field)
        if o.grant == "client_credentials":
            fresh = await self._token_request(o.token_url, {"grant_type": "client_credentials", "scope": " ".join(o.scopes), **client})
        elif tokens and tokens.get("refresh_token"):
            fresh = await self._token_request(o.token_url, {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], **client})
        else:
            raise CloudError(f"Sign in to {m.name} first, with the Sign in button on External Service.")
        self._save_tokens(m.id, fresh)
        return fresh["access_token"]

    # ---- calling an operation ----

    def _render(self, op: Operation, params: dict[str, Any]) -> tuple[str, dict[str, str], Any]:
        declared = {p.name: p for p in op.params}
        unknown = set(params) - set(declared)
        if unknown:
            raise CloudError(f"{op.name} doesn't take {', '.join(sorted(unknown))}. It takes: {', '.join(declared) or 'nothing'}.")
        missing = [p.name for p in op.params if p.required and p.name not in params]
        if missing:
            raise CloudError(f"{op.name} needs {', '.join(missing)}.")

        def text(template: str, quote: bool) -> str:
            return PLACEHOLDER.sub(lambda m_: urllib.parse.quote(str(params.get(m_.group(1), "")), safe="") if quote else str(params.get(m_.group(1), "")), template)

        def body(node: Any) -> Any:
            if isinstance(node, str):
                whole = PLACEHOLDER.fullmatch(node)
                return params.get(whole.group(1)) if whole else text(node, False)
            if isinstance(node, list):
                return [body(n) for n in node]
            if isinstance(node, dict):
                return {k: body(v) for k, v in node.items()}
            return node

        query = {k: text(v, False) for k, v in op.query.items() if not (PLACEHOLDER.fullmatch(v) and PLACEHOLDER.fullmatch(v).group(1) not in params)}
        return text(op.path, True), query, body(op.body)

    def _redact(self, m: Manifest, text: str) -> str:
        values = [self._key(m.id, f.id) for f in m.fields]
        tokens = self._oauth_tokens(m.id) or {}
        for v in [*values, tokens.get("access_token"), tokens.get("refresh_token")]:
            if v and len(v) >= 6:
                text = text.replace(v, "[key hidden]")
        return text

    def _rate(self, conn_id: str) -> None:
        now = time.time()
        recent = [t for t in self._calls.get(conn_id, []) if now - t < 60]
        if len(recent) >= CALLS_PER_MINUTE:
            raise CloudError(f"More than {CALLS_PER_MINUTE} calls to this service in a minute. Wait a moment.")
        self._calls[conn_id] = [*recent, now]

    async def call(self, conn_id: str, operation: str, params: dict[str, Any], actor: Actor, project: str = "", allow_writes: bool = False) -> dict[str, Any]:
        """Runs one approved operation. Writes only run with allow_writes, which only a person's approval sets."""
        m = self.approved(conn_id)
        if not m:
            raise CloudError(f"There's no approved connection called {conn_id}.")
        op = next((o for o in m.operations if o.name == operation), None)
        if not op:
            raise CloudError(f"{m.name} has no operation {operation}. It has: {', '.join(o.name for o in m.operations)}.")
        if op.writes and not allow_writes:
            raise CloudError(f"{operation} changes something in {m.name}, so it needs the person's approval each time.")
        missing = self.missing_keys(m)
        if missing:
            raise CloudError(f"{m.name} isn't set up yet: fill in {', '.join(missing)} on External Service.")
        self._rate(conn_id)
        started = time.time()
        try:
            result = await (self._call_mcp(m, op, params) if m.kind == "mcp" else self._call_api(m, op, params))
            ok, detail = result["ok"], result
        except CloudError:
            raise
        except Exception as exc:  # noqa: BLE001 - the reason goes back in plain words
            ok, detail = False, {"ok": False, "error": f"{type(exc).__name__}: {self._redact(m, str(exc))[:400]}"}
        empty = bool(ok) and _empty(detail.get("data"))
        self.ledger.append(actor, "conn.called", id=conn_id, operation=operation, project=project, ok=ok, status=detail.get("status"), seconds=round(time.time() - started, 2),
                           params={k: str(v)[:80] for k, v in params.items()}, empty=empty)
        if empty:
            detail = {**detail, "note": "It succeeded but sent nothing back. If you need its results, have it save them to a file in the project folder and read that file."}
        return detail

    def returns_nothing(self, conn_id: str) -> set[str]:
        """Operations whose last two successful calls returned nothing, such as a script runner that doesn't send back
        the script's output. Agents are told, so the next project doesn't fall into the same trap."""
        seen: dict[str, list[bool]] = {}
        for e in self.ledger.events():
            d = e["data"]
            if e["kind"] == "conn.called" and d["id"] == conn_id and d.get("ok"):
                seen.setdefault(d["operation"], []).append(bool(d.get("empty")))
        return {op for op, results in seen.items() if len(results) >= 2 and all(results[-2:])}

    async def _auth(self, m: Manifest) -> tuple[dict[str, str], dict[str, str]]:
        a, headers, query = m.auth, {}, {}
        if a.type == "header":
            headers[a.header_name] = self._key(m.id, a.field) or ""
        elif a.type == "bearer":
            headers["Authorization"] = f"Bearer {self._key(m.id, a.field)}"
        elif a.type == "query":
            query[a.query_name] = self._key(m.id, a.field) or ""
        elif a.type == "oauth2":
            headers["Authorization"] = f"Bearer {await self._access_token(m)}"
        return headers, query

    async def _call_api(self, m: Manifest, op: Operation, params: dict[str, Any]) -> dict[str, Any]:
        import httpx

        path, query, body = self._render(op, params)
        url = m.base_url.rstrip("/") + path
        if urllib.parse.urlsplit(url).hostname != urllib.parse.urlsplit(m.base_url).hostname:
            raise CloudError("That request would leave the approved address.")
        headers, auth_query = await self._auth(m)
        headers = {"Accept": "application/json", "User-Agent": "BuildAnything", **headers}
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            r = await client.request(op.method, url, params={**query, **auth_query}, json=body if body is not None else None, headers=headers)
        text = self._redact(m, r.text)
        try:
            data: Any = json.loads(text)
        except ValueError:
            data = text[:MAX_RESULT]
        out = json.dumps(data, default=str)
        if len(out) > MAX_RESULT:
            data, truncated = out[:MAX_RESULT], True
        else:
            truncated = False
        ok = r.status_code < 400
        reply: dict[str, Any] = {"ok": ok, "status": r.status_code, "data": data, "truncated": truncated}
        if not ok:
            reply["error"] = f"{m.name} answered {r.status_code}" + (" (the key may be wrong or expired)" if r.status_code in (401, 403) else "")
        return reply

    async def _call_mcp(self, m: Manifest, op: Operation, params: dict[str, Any]) -> dict[str, Any]:
        from fastmcp import Client
        from fastmcp.client.transports import SSETransport, StdioTransport, StreamableHttpTransport

        from .connectors import clean_environment, parse_target

        spec = parse_target(m.mcp_target)
        headers, _ = await self._auth(m)
        if "url" in spec:
            transport: Any = (SSETransport if spec["url"].rstrip("/").endswith("/sse") else StreamableHttpTransport)(spec["url"], headers=headers or None)
        else:
            env = clean_environment()
            if m.auth.type == "env":
                env.update({f.id.upper(): self._key(m.id, f.id) or "" for f in m.fields})
            transport = StdioTransport(command=spec["command"], args=spec["args"], env=env)
        async with Client(transport) as client:
            _path, _query, template = self._render(op, params)  # fixed arguments from the template; unknown ones refused
            result = await client.call_tool(op.mcp_tool, template if isinstance(op.body, dict) else params)
        data = result.data if getattr(result, "data", None) is not None else [getattr(c, "text", str(c)) for c in result.content]
        out = self._redact(m, json.dumps(data, default=str))
        return {"ok": not getattr(result, "is_error", False), "status": "mcp", "data": json.loads(out) if len(out) <= MAX_RESULT else out[:MAX_RESULT], "truncated": len(out) > MAX_RESULT}

    async def list_mcp_tools(self, target: str) -> list[dict[str, Any]]:
        """Read-only: what an MCP server offers, so External Service can describe its operations."""
        from .connectors import list_mcp_tools, parse_target

        return await list_mcp_tools(parse_target(target))

    # ---- changes wait for the person ----

    def request_write(self, conn_id: str, operation: str, params: dict[str, Any], why: str, project: str, department: str, actor: Actor) -> dict[str, Any]:
        m = self.approved(conn_id)
        op = next((o for o in (m.operations if m else []) if o.name == operation), None)
        if not m or not op:
            raise CloudError("Unknown connection or operation.")
        event = self.ledger.append(actor, "conn.write_requested", id=conn_id, operation=operation, params=params, why=why.strip()[:600], project=project, department=department)
        return {"request": event["id"], "status": "waiting"}

    def requests(self) -> list[dict[str, Any]]:
        decided = {e["data"]["request"]: e for e in self.ledger.events() if e["kind"] == "conn.write_decided"}
        results = {e["data"].get("request"): e["data"] for e in self.ledger.events() if e["kind"] == "conn.write_result"}
        out = []
        for e in self.ledger.events():
            if e["kind"] == "conn.write_requested":
                d = decided.get(e["id"])
                out.append({**e["data"], "request": e["id"], "by": e["actor"]["name"], "at": e["at"],
                            "status": "waiting" if not d else ("approved" if d["data"]["approved"] else "rejected"), "result": results.get(e["id"])})
        return out[::-1]

    async def decide_write(self, request_id: str, approve: bool, reason: str, actor: Actor) -> dict[str, Any]:
        req = next((r for r in self.requests() if r["request"] == request_id), None)
        if not req or req["status"] != "waiting":
            raise CloudError("That request isn't waiting.")
        self.ledger.append(actor, "conn.write_decided", request=request_id, approved=approve, reason=reason.strip()[:600])
        if not approve:
            return {"status": "rejected"}
        result = await self.call(req["id"], req["operation"], req["params"], actor, req.get("project", ""), allow_writes=True)
        self.ledger.append(Actor.system("connections"), "conn.write_result", request=request_id, ok=result.get("ok"), status=result.get("status"), summary=json.dumps(result.get("data"), default=str)[:1500])
        return result

    # ---- views ----

    def listing(self) -> list[dict[str, Any]]:
        out = []
        if not self.root.is_dir():
            return out
        recent: dict[str, dict[str, Any]] = {}
        for e in self.ledger.events():
            if e["kind"] == "conn.called":
                recent[e["data"]["id"]] = {"at": e["at"], "ok": e["data"]["ok"], "operation": e["data"]["operation"], "status": e["data"].get("status")}
        for folder in sorted(p for p in self.root.iterdir() if p.is_dir()):
            approved, proposed = self._read(folder.name, "approved"), self._read(folder.name, "proposed")
            m = Manifest(**approved) if approved else None
            state = "waiting" if proposed and not approved else "needs_setup" if m and (self.missing_keys(m) or (m.auth.type == "oauth2" and m.auth.oauth and m.auth.oauth.grant == "authorization_code" and not self._oauth_tokens(m.id))) else "connected" if m else "none"
            if state == "none":
                continue
            out.append({"id": folder.name, "state": state, "approved": approved, "proposed": proposed, "missing": self.missing_keys(m) if m else [],
                        "signedIn": bool(self._oauth_tokens(folder.name)), "last": recent.get(folder.name)})
        return out

    def for_department(self, department: str) -> list[dict[str, Any]]:
        """What a department's agent may use: connected services and their operations."""
        out = []
        for c in self.listing():
            m = c["approved"]
            if m and department in m["departments"]:
                hollow = self.returns_nothing(m["id"])
                out.append({"connection": m["id"], "name": m["name"], "summary": m["summary"], "ready": c["state"] == "connected",
                            "operations": [{"name": o["name"], "description": o["description"], "changes_something": o["writes"], "params": {p["name"]: f"{p['type']}{'' if p['required'] else ', optional'}: {p['description']}" for p in o["params"]},
                                            **({"warning": "Its recent calls succeeded but sent nothing back. It still works for things it does on this computer: to get results, make what it runs save them to a file in the project folder (such as mechanics/cad/ for a URDF export, or a JSON file of measurements), then read that file."} if o["name"] in hollow else {})} for o in m["operations"]]})
        return out
