import pytest

from buildanything import services as sv
from buildanything.ledger import Actor, Ledger, PermissionDenied

HUMAN = Actor.human()


@pytest.fixture
def app(tmp_path):
    return Ledger(tmp_path / "_app")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "firmware" / "laptop").mkdir(parents=True)
    (root / "firmware" / "laptop" / "app.py").write_text("import cv2\nimport numpy as np\nimport os\nfrom model import load\n", encoding="utf-8")
    (root / "firmware" / "laptop" / "model.py").write_text("import torch\n", encoding="utf-8")
    (root / "firmware" / "board").mkdir()
    (root / "firmware" / "board" / "platformio.ini").write_text("[env:x]\nplatform = espressif32\nboard = esp32-c6-devkitc-1\nlib_deps =\n  adafruit/Adafruit NeoPixel\n", encoding="utf-8")
    return root


def test_no_tools_are_listed_or_connected(app):
    assert sv.CATALOG == [] and sv.all_tools() == [] and sv.entry_for("mujoco") is None and sv.entry_for("fusion") is None
    app.append(HUMAN, "service.tool_decided", id="roblox-studio", approved=True, reason="")
    sv.load_custom(app)
    assert sv.entry_for("roblox-studio") is None  # tools added earlier through proposals aren't loaded either


def test_a_project_needs_what_its_code_imports(project):
    view = sv.project_view(project)
    assert [p["package"] for p in view["python"]["packages"]] == ["opencv-python", "numpy", "torch"]  # not os, not its own model.py
    assert view["python"]["environment"] is None and set(view["python"]["missing"]) == {"opencv-python", "numpy", "torch"}
    assert view["firmware"][0]["board"] == "esp32-c6-devkitc-1" and "adafruit/Adafruit NeoPixel" in view["firmware"][0]["libraries"]


def test_package_names_are_checked_before_anything_runs(project):
    with pytest.raises(sv.ServiceError):
        sv.plan("project-python", project, ["numpy; rm -rf /"])
    with pytest.raises(sv.ServiceError):
        sv.plan("project-python", project, ["--index-url=http://evil"])
    step = sv.plan("project-python", project, ["opencv-python==4.10.0.84"])
    assert step["steps"][-1][-1] == "opencv-python==4.10.0.84" and len(step["steps"]) == 2  # makes the environment first
    with pytest.raises(sv.ServiceError):
        sv.plan("nexar", None, None)  # a key isn't installed


def test_an_agent_proposes_and_only_a_person_approves(app, project, monkeypatch):
    ran = []
    monkeypatch.setattr(sv.Installs, "_run", lambda self, rid, steps, service: ran.append(steps))
    installs = sv.Installs(app)
    made = installs.propose("project-python", project, ["numpy"], "The laptop code imports numpy.", Actor.agent("internal_service"))
    assert installs.requests()[0]["status"] == "waiting" and not ran
    with pytest.raises(PermissionDenied):
        app.append(Actor.agent("internal_service"), "service.install_approved", id=made["id"])
    installs.approve(made["id"], HUMAN, {"proj": project})
    installs.running[made["id"]].join(5)
    assert ran and ran[0][-1][-1] == "numpy"
    with pytest.raises(sv.ServiceError):
        installs.approve(made["id"], HUMAN, {"proj": project})  # only once


def test_departments_can_ask_for_a_tool(app):
    assert "specifically" in sv.ask(app, "proj", "Mechanical", "structural analysis", "fea")
    assert sv.ask(app, "proj", "Mechanical", "fea.frame", "Check member forces in the bridge truss.").startswith("Asked")
    assert "already" in sv.ask(app, "proj", "Mechanical", "fea.frame", "Check member forces in the bridge truss.")
    assert sv.asks(app)[0]["department"] == "Mechanical" and sv.providers_of("fea.frame") == []
