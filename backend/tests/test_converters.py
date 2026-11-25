import asyncio

import pytest

from buildanything import converters as cv
from buildanything.ledger import Actor, Ledger, PermissionDenied

HUMAN = Actor.human()
GOOD = '''
def convert(path):
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    return {"kind": "markdown", "text": f"**{len(lines)} lines**"}
'''


@pytest.fixture
def world(tmp_path):
    app = tmp_path / "_app"
    app.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    (project / "part.step").write_text("ISO-10303-21;\nHEADER;\nENDSEC;\n", encoding="utf-8")
    (project / "other.txt").write_text("private", encoding="utf-8")
    return tmp_path, app, project


def write(app, code, name="convert.py"):
    d = cv.folder(app, ".step")
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(code, encoding="utf-8")
    return d / name


def test_a_converter_runs_in_the_sandbox(world):
    projects, app, project = world
    out = cv.run(app, write(app, GOOD, "draft.py"), project / "part.step", projects)
    assert out["ok"] and out["result"] == {"kind": "markdown", "text": "**3 lines**"}


def test_the_sandbox_reads_only_the_given_file_and_writes_only_scratch(world):
    projects, app, project = world
    sneaky_read = f'def convert(path):\n    return {{"kind": "text", "text": open(r"{project / "other.txt"}").read()}}\n'
    assert "only read the one file" in cv.run(app, write(app, sneaky_read, "draft.py"), project / "part.step", projects)["error"]
    sneaky_write = f'def convert(path):\n    open(r"{project / "planted.py"}", "w").write("x")\n    return {{"kind": "text", "text": "done"}}\n'
    assert "only write in their own scratch" in cv.run(app, write(app, sneaky_write, "draft.py"), project / "part.step", projects)["error"]
    assert not (project / "planted.py").exists()
    network = 'import socket\ndef convert(path):\n    socket.create_connection(("example.com", 80))\n    return {"kind": "text", "text": "x"}\n'
    assert "network" in cv.run(app, write(app, network, "draft.py"), project / "part.step", projects)["error"]


def test_output_must_match_a_viewer(world):
    projects, app, project = world
    bad = 'def convert(path):\n    return {"kind": "video"}\n'
    assert "kind is one of" in cv.run(app, write(app, bad, "draft.py"), project / "part.step", projects)["error"]


def test_only_tested_code_is_proposed_and_only_a_person_approves(world):
    projects, app, project = world
    state = {}
    tools = {t.__name__: t for t in cv.tools(app, projects, project / "part.step", "proj", state)}
    assert tools["inspect_file"]()["looks_like_text"]
    tools["write_converter"](GOOD)
    assert "error" in tools["propose_converter"]("Shows line count")  # not tested yet
    assert tools["test_converter"]()["ok"]
    assert tools["propose_converter"]("Shows line count")["proposed"]
    assert cv.meta(app, ".step")["status"] == "proposed" and cv.approved(app, ".step") is None
    app_ledger = Ledger(app)
    with pytest.raises(PermissionDenied):
        app_ledger.append(Actor.agent("platform"), "platform.converter_decided", type=".step", approved=True, code_hash="x", reason="")
    cv.decide(app, app_ledger, ".step", True, "", HUMAN)
    assert cv.approved(app, ".step") is not None
    assert cv.view(app, projects, project / "part.step")["result"]["text"] == "**3 lines**"


def test_an_approved_converter_stops_working_if_its_code_is_changed(world):
    projects, app, project = world
    state = {}
    tools = {t.__name__: t for t in cv.tools(app, projects, project / "part.step", "proj", state)}
    tools["write_converter"](GOOD)
    tools["test_converter"]()
    tools["propose_converter"]("x")
    cv.decide(app, Ledger(app), ".step", True, "", HUMAN)
    write(app, GOOD.replace("lines**", "LINES**"))  # edited after approval
    assert cv.approved(app, ".step") is None


def test_make_reports_when_the_agent_gives_up(world):
    projects, app, project = world

    class Gateway:
        async def run(self, **_):
            return "I couldn't parse it."

    led = Ledger(project)
    with pytest.raises(cv.ConverterError):
        asyncio.run(cv.make(app, projects, led, Gateway(), project / "part.step"))
