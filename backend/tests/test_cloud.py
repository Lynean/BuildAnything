import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from buildanything import cloud
from buildanything.ledger import Actor, Ledger, PermissionDenied

HUMAN = Actor.human()
AGENT = Actor.agent("external_service")
SEEN: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        SEEN.append({"method": "GET", "path": self.path, "auth": self.headers.get("Authorization")})
        if self.headers.get("Authorization") != "Bearer tok-123456":
            return self._reply(401, {"message": "bad credentials"})
        if self.path.startswith("/me"):
            return self._reply(200, {"login": "maker", "echo": "tok-123456"})
        return self._reply(200, {"path": self.path})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode()
        SEEN.append({"method": "POST", "path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if self.path == "/token":
            return self._reply(200, {"access_token": "cc-token-999999", "expires_in": 3600})
        return self._reply(200, {"created": json.loads(body or "{}")})


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


class Secrets:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def api_manifest(base, **extra):
    return {"id": "demo", "name": "Demo", "summary": "A test service.", "kind": "api", "base_url": base,
            "fields": [{"id": "token", "label": "Token", "where": "Settings"}], "auth": {"type": "bearer", "field": "token"},
            "setup": ["Make a token.", "Paste it."], "departments": ["Software"], "check": "me",
            "operations": [
                {"name": "me", "description": "Who am I.", "writes": False, "path": "/me"},
                {"name": "issue", "description": "One issue.", "writes": False, "path": "/repos/{owner}/issues/{number}", "params": [{"name": "owner"}, {"name": "number", "type": "integer"}]},
                {"name": "create_note", "description": "Create a note.", "writes": True, "method": "POST", "path": "/notes", "body": {"text": "{text}", "tags": "{tags}"}, "params": [{"name": "text"}, {"name": "tags", "type": "array", "required": False}]},
            ], **extra}


@pytest.fixture
def store(tmp_path):
    return cloud.Store(Ledger(tmp_path / "_app"), Secrets())


def run(coro):
    return asyncio.run(coro)


def test_bad_descriptions_are_refused(store):
    with pytest.raises(cloud.CloudError, match="https"):
        store.propose(api_manifest("http://example.com"), "", AGENT)  # plain http only on this computer
    bad = api_manifest("https://api.example.com")
    bad["operations"][2]["writes"] = False
    bad["operations"][2]["method"] = "DELETE"
    with pytest.raises(cloud.CloudError, match="writes must be true"):
        store.propose(bad, "", AGENT)
    bad = api_manifest("https://api.example.com")
    bad["operations"][1]["path"] = "/repos/{owner}/{secret}"
    with pytest.raises(cloud.CloudError, match="not listed in params"):
        store.propose(bad, "", AGENT)
    bad = api_manifest("https://api.example.com", check="create_note")
    with pytest.raises(cloud.CloudError, match="only reads"):
        store.propose(bad, "", AGENT)


def test_only_a_person_approves_and_types_keys(store, server):
    store.propose(api_manifest(server), "The person asked.", AGENT)
    with pytest.raises(PermissionDenied):
        store.decide("demo", True, "", AGENT)
    store.decide("demo", True, "", HUMAN)
    with pytest.raises(PermissionDenied):
        store.set_key("demo", "token", "tok-123456", AGENT)
    assert store.listing()[0]["state"] == "needs_setup"
    store.set_key("demo", "token", "tok-123456", HUMAN)
    assert store.listing()[0]["state"] == "connected"
    assert "tok-123456" not in json.dumps(store.ledger.events())  # the key is never written to the record
    with pytest.raises(PermissionDenied):
        store.remove("demo", AGENT)
    assert store.listing()[0]["state"] == "connected"  # an agent can't delete keys either


def test_calls_add_the_key_render_params_and_hide_the_key_in_answers(store, server):
    store.propose(api_manifest(server), "", AGENT)
    store.decide("demo", True, "", HUMAN)
    with pytest.raises(cloud.CloudError, match="isn't set up"):
        run(store.call("demo", "me", {}, AGENT))
    store.set_key("demo", "token", "tok-123456", HUMAN)
    me = run(store.call("demo", "me", {}, AGENT))
    assert me["ok"] and me["data"]["login"] == "maker" and me["data"]["echo"] == "[key hidden]"
    issue = run(store.call("demo", "issue", {"owner": "a b/c", "number": 7}, AGENT))
    assert issue["data"]["path"] == "/repos/a%20b%2Fc/issues/7"  # placeholders are escaped, so they can't change the path
    with pytest.raises(cloud.CloudError, match="doesn't take"):
        run(store.call("demo", "issue", {"owner": "x", "number": 1, "evil": 1}, AGENT))
    store.set_key("demo", "token", "wrong-token", HUMAN)
    failed = run(store.call("demo", "me", {}, AGENT))
    assert not failed["ok"] and "key may be wrong" in failed["error"]


def test_changes_wait_for_the_person_every_time(store, server):
    store.propose(api_manifest(server), "", AGENT)
    store.decide("demo", True, "", HUMAN)
    store.set_key("demo", "token", "tok-123456", HUMAN)
    with pytest.raises(cloud.CloudError, match="approval"):
        run(store.call("demo", "create_note", {"text": "hi"}, AGENT))
    req = store.request_write("demo", "create_note", {"text": "hi", "tags": ["a", "b"]}, "Log the test result.", "proj", "Software", Actor.agent("software"))
    assert store.requests()[0]["status"] == "waiting"
    with pytest.raises(PermissionDenied):
        run(store.decide_write(req["request"], True, "", AGENT))
    result = run(store.decide_write(req["request"], True, "", HUMAN))
    assert result["ok"] and result["data"]["created"] == {"text": "hi", "tags": ["a", "b"]}  # a whole-value placeholder keeps its type
    assert store.requests()[0]["status"] == "approved" and store.requests()[0]["result"]["ok"]
    with pytest.raises(cloud.CloudError):
        run(store.decide_write(req["request"], True, "", HUMAN))  # once only


def test_client_credentials_get_a_token_without_the_person_signing_in(store, server):
    m = api_manifest(server)
    m["fields"] = [{"id": "client_id", "label": "Client ID", "secret": False}, {"id": "client_secret", "label": "Client secret"}]
    m["auth"] = {"type": "oauth2", "oauth": {"grant": "client_credentials", "token_url": f"{server}/token", "scopes": ["read"], "client_id_field": "client_id", "client_secret_field": "client_secret"}}
    store.propose(m, "", AGENT)
    store.decide("demo", True, "", HUMAN)
    store.set_key("demo", "client_id", "app", HUMAN)
    store.set_key("demo", "client_secret", "shh-secret", HUMAN)
    SEEN.clear()
    run(store.call("demo", "issue", {"owner": "o", "number": 1}, AGENT))
    assert "client_secret=shh-secret" in SEEN[0]["body"] and SEEN[1]["auth"] == "Bearer cc-token-999999"


def test_browser_sign_in_needs_a_started_link(store):
    m = api_manifest("https://api.example.com")
    m["fields"] = [{"id": "client_id", "label": "Client ID", "secret": False}]
    m["auth"] = {"type": "oauth2", "oauth": {"grant": "authorization_code", "authorize_url": "https://id.example.com/authorize", "token_url": "https://id.example.com/token", "scopes": ["openid"], "client_id_field": "client_id"}}
    store.propose(m, "", AGENT)
    store.decide("demo", True, "", HUMAN)
    store.set_key("demo", "client_id", "app-1", HUMAN)
    url = store.oauth_start("demo", "http://127.0.0.1:8787/api/connections/oauth/callback")
    assert url.startswith("https://id.example.com/authorize?") and "code_challenge_method=S256" in url and "client_id=app-1" in url
    with pytest.raises(cloud.CloudError, match="expired or wasn't started"):
        run(store.oauth_finish("forged-state", "code"))
    assert store.listing()[0]["state"] == "needs_setup"  # not signed in yet


def test_an_mcp_server_is_called_by_its_tools(store, tmp_path):
    server = tmp_path / "mcp_server.py"
    server.write_text("from fastmcp import FastMCP\nmcp = FastMCP('t')\n@mcp.tool\ndef add(a: int, b: int) -> int:\n    return a + b\nif __name__ == '__main__':\n    mcp.run()\n", encoding="utf-8")
    m = {"id": "calc", "name": "Calculator", "summary": "Adds.", "kind": "mcp", "mcp_target": f'"{sys.executable}" "{server}"', "fields": [], "auth": {"type": "none"},
         "departments": ["Software"], "check": "add_numbers", "operations": [{"name": "add_numbers", "description": "Adds two numbers.", "writes": False, "mcp_tool": "add", "params": [{"name": "a", "type": "integer"}, {"name": "b", "type": "integer"}]}]}
    store.propose(m, "", AGENT)
    store.decide("calc", True, "", HUMAN)
    assert run(store.call("calc", "add_numbers", {"a": 2, "b": 3}, AGENT))["data"] == 5


def test_department_agents_see_their_services_and_request_changes(tmp_path, server):
    from buildanything import headquarters as hq

    app = Ledger(tmp_path / "_app")
    store = cloud.Store(app, Secrets())
    store.propose(api_manifest(server), "", AGENT)
    store.decide("demo", True, "", HUMAN)
    store.set_key("demo", "token", "tok-123456", HUMAN)
    project = Ledger(tmp_path / "proj")
    assert hq._services_for(project, "Software")[0]["connection"] == "demo"
    assert isinstance(hq._services_for(project, "Mechanical"), str)  # not connected for Mechanical
    use = hq._service_tool(project, "Software")
    assert run(use("demo", "me"))["ok"]
    assert "reason" in run(use("demo", "create_note", '{"text": "x"}'))["error"]
    assert run(use("demo", "create_note", '{"text": "x"}', "Record the result."))["status"] == "waiting"
    assert "isn't connected for Mechanical" in run(hq._service_tool(project, "Mechanical")("demo", "me"))["error"]


def test_every_example_connection_is_valid():
    from buildanything import tabs

    for name in ("connection-github", "connection-altium-365", "connection-fusion-mcp"):
        cloud.validate(json.loads(tabs.example(name)["connection.json"]))


def test_an_mcp_action_can_pin_arguments_so_test_works_and_read_only_is_enforced(store, tmp_path):
    server = tmp_path / "echo_server.py"
    server.write_text("from fastmcp import FastMCP\nmcp = FastMCP('t')\n@mcp.tool\ndef run(queryType: str, readOnly: bool, script: str = '') -> dict:\n    return {'queryType': queryType, 'readOnly': readOnly, 'script': script}\nif __name__ == '__main__':\n    mcp.run()\n", encoding="utf-8")
    m = {"id": "echo", "name": "Echo", "summary": "Echoes.", "kind": "mcp", "mcp_target": f'"{sys.executable}" "{server}"', "fields": [], "auth": {"type": "none"}, "departments": ["Mechanical"], "check": "documents",
         "operations": [
             {"name": "documents", "description": "List documents.", "writes": False, "mcp_tool": "run", "body": {"queryType": "document", "readOnly": True}},
             {"name": "inspect", "description": "Run a read-only script.", "writes": False, "mcp_tool": "run", "body": {"queryType": "script", "readOnly": True, "script": "{script}"}, "params": [{"name": "script"}]},
         ]}
    store.propose(m, "", AGENT)
    store.decide("echo", True, "", HUMAN)
    assert run(store.call("echo", "documents", {}, AGENT))["data"] == {"queryType": "document", "readOnly": True, "script": ""}  # Test can run it
    out = run(store.call("echo", "inspect", {"script": "print(1)"}, AGENT))["data"]
    assert out["readOnly"] is True and out["script"] == "print(1)"
    with pytest.raises(cloud.CloudError, match="doesn't take"):
        run(store.call("echo", "inspect", {"script": "x", "readOnly": False}, AGENT))  # the agent can't turn read-only off
