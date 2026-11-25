import pytest

from buildanything import tabs
from buildanything.ledger import Actor, Ledger, PermissionDenied

HUMAN = Actor.human()
AGENT = Actor.agent("internal_service")


@pytest.fixture
def app(tmp_path):
    return Ledger(tmp_path / "_app")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "log.csv").write_text("t,v\n0,1\n1,3\n2,2\n", encoding="utf-8")
    (root / ".env").write_text("KEY=secret", encoding="utf-8")
    return root


def write_example(app, tab_id, name):
    for path, text in tabs.example(name).items():
        tabs.write_file(app.root, tab_id, path, text)


def test_every_example_follows_the_rules_and_compiles(app):
    for name in ("fusion", "mujoco", "kicad", "matlab", "plot"):
        write_example(app, name, name)
        assert tabs.lint(tabs.read_files(app.root, name)) == [], name
        assert tabs.build(app.root, name)["ok"], name


def test_the_rules_catch_network_glow_and_storage(app):
    tabs.write_file(app.root, "bad", "Tab.tsx", 'export default function Tab() { fetch("http://x"); localStorage.x = 1; return <div style={{ boxShadow: "0 0 22px 2px rgba(1,2,3,.5)" }} />; }')
    tabs.write_file(app.root, "bad", "adapter.py", "import requests\ndef collect(project, out, tool):\n    return {}\n")
    problems = " ".join(tabs.lint(tabs.read_files(app.root, "bad")))
    assert "network" in problems and "storage" in problems and "glow" in problems and "Data scripts have no network" in problems


def test_tabs_may_import_only_the_sdk_react_and_three(app):
    tabs.write_file(app.root, "imp", "Tab.tsx", 'import fs from "fs";\nexport default function Tab() { return <p>{String(fs)}</p>; }')
    result = tabs.build(app.root, "imp")
    assert not result["ok"]
    with pytest.raises(tabs.TabError):
        tabs.write_file(app.root, "imp", "../escape.tsx", "x")


def test_only_a_person_approves_and_an_edit_keeps_the_approved_copy(app, project):
    write_example(app, "plot", "plot")
    tabs.propose(app.root, app, "plot", "CSV", "Software", "Data", "Plots the logs.", "", "The person asked.", AGENT)
    with pytest.raises(PermissionDenied):
        app.append(AGENT, "tab.decided", id="plot", approved=True, reason="", hash="x")
    with pytest.raises(PermissionDenied):
        tabs.set_shown(app, "plot", "proj", True, AGENT)
    tabs.decide(app.root, app, "plot", True, "", HUMAN)
    assert tabs.for_department(app.root, app, "proj", "Software") == []  # approved, but not shown on this project yet
    tabs.set_shown(app, "plot", "proj", True, HUMAN)
    assert [t["title"] for t in tabs.for_department(app.root, app, "proj", "Software")] == ["Data"]
    tabs.write_file(app.root, "plot", "README.md", "changed")
    meta = next(t for t in tabs.listing(app.root, app) if t["id"] == "plot")
    assert meta["status"] == "draft" and meta["intact"]  # the approved copy keeps working until a new version is approved
    assert tabs.for_department(app.root, app, "proj", "Software")


def test_a_tab_that_doesnt_build_or_names_a_missing_program_cant_be_proposed(app):
    tabs.write_file(app.root, "broken", "Tab.tsx", "export default function Tab() { return <div> }")
    with pytest.raises(tabs.TabError):
        tabs.propose(app.root, app, "broken", "x", "Mechanical", "Broken", "", "", "", AGENT)
    write_example(app, "kicad", "kicad")
    with pytest.raises(tabs.TabError, match="isn't on this computer"):
        tabs.propose(app.root, app, "kicad", "KiCad", "Electrical", "KiCad checks", "", "no-such-program-xyz", "", AGENT)


def test_data_scripts_need_the_persons_switch_and_stay_in_the_sandbox(app, project):
    write_example(app, "plot", "plot")
    assert "switched off" in tabs.run(app.root, app, "plot", project, "proposed")["error"]
    tabs.set_scripts_allowed(app, True, HUMAN)
    with pytest.raises(PermissionDenied):
        tabs.set_scripts_allowed(app, True, AGENT)
    result = tabs.run(app.root, app, "plot", project, "proposed")
    assert result["ok"] and result["output"]["tables"][0]["stats"][1]["max"] == 3.0
    tabs.write_file(app.root, "plot", "adapter.py", "def collect(project, out, tool):\n    (project / 'data' / 'log.csv').write_text('gone')\n    return {}\n")
    assert "read-only" in tabs.run(app.root, app, "plot", project, "proposed")["error"]
    tabs.write_file(app.root, "plot", "adapter.py", "def collect(project, out, tool):\n    return {'k': (project / '.env').read_text()}\n")
    assert "secrets" in tabs.run(app.root, app, "plot", project, "proposed")["error"]
    tabs.write_file(app.root, "plot", "adapter.py", "def collect(project, out, tool):\n    return tool.run(['x'])\n")
    assert "without a program" in tabs.run(app.root, app, "plot", project, "proposed")["error"]


def test_tabs_read_only_this_projects_files_and_never_secrets(project):
    assert tabs.read_project_file(project, "data/log.csv")["text"].startswith("t,v")
    with pytest.raises(tabs.TabError):
        tabs.read_project_file(project, ".env")
    with pytest.raises(tabs.TabError):
        tabs.read_project_file(project, "../other/file.txt")
    assert [f["path"] for f in tabs.list_project_files(project)] == ["data/log.csv"]


def test_the_service_agents_get_both_skills():
    text = tabs.builtin_skills()
    assert "# Tab-creator" in text and "# Card-creator" in text and "replay what was recorded" in text


def test_a_running_agent_keeps_a_short_trail_for_the_person(tmp_path):
    from types import SimpleNamespace as NS

    from buildanything import gateway, jobs

    led = Ledger(tmp_path / "proj")
    live = jobs.agent_began(led, "lead_mechanical", "Mechanical", "Mechanical Lead")
    spec = NS(tools={"propose_part": "Suggest a part for the person to add."})
    response = NS(output=[NS(type="reasoning", summary=[NS(text="Check the load first.")]),
                          NS(type="function_call", name="propose_part", arguments='{"number": "B-09", "name": "Gusset", "script": "secret long text"}'),
                          NS(type="message", content=[NS(text="I proposed a gusset.")])])
    gateway._trail(live, spec, response)
    trail = jobs.trail(led, live.id)
    assert [s["kind"] for s in trail["steps"]] == ["thought", "tool", "said"]
    assert "Suggest a part" in trail["steps"][1]["text"] and "B-09" in trail["steps"][1]["text"] and "secret" not in trail["steps"][1]["text"]
    assert jobs.running(led)[0]["title"] == "Mechanical Lead"
    for n in range(100):
        jobs.step(live, "thought", f"step {n}")
    assert len(jobs.trail(led, live.id)["steps"]) == jobs.MAX_STEPS  # it stays short
    jobs.agent_ended(led, live)
    assert jobs.trail(led, live.id) is None and jobs.running(led) == []
