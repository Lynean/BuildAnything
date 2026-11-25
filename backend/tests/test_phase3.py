"""Offline tests, using the failure replays from past projects."""
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fake_model import TestModel

from buildanything import api, geometry, importer, mechanical as mech, procurement as proc, providers as prov
from buildanything.gateway import Price
from buildanything.ledger import Actor, Ledger, PermissionDenied

HUMAN = Actor.human()


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.append(HUMAN, "project.created", name="Robot", aim="A", scope="S")
    return ledger


def deck(led, **extra):
    mech.add_part(led, {"number": "P12", "name": "Deck", "material": "Al 6061-T6", "process": "Machined", "qty": 1, "mass_g": 727.0, "unit_cost": 40.0, **extra}, HUMAN)


# ---- Replays from past projects ----


def test_replay_aluminium_to_acrylic_reopens_strength_and_mass(led):
    deck(led)
    impact = mech.revise(led, "P12", {"material": "Cast acrylic"}, "Budget", HUMAN)
    assert impact["revision"] == "B"
    assert {"Strength", "Mass", "Cost"} <= set(impact["reopened"])
    assert {c["check"] for c in mech.open_checks(led)} >= {"Strength", "Mass", "Cost"}


def test_replay_superseded_parts_never_appear_in_an_export(led, tmp_path):
    deck(led)
    mech.revise(led, "P12", {"material": "Cast acrylic"}, "Budget", HUMAN)
    release = mech.create_release(led, tmp_path, "Workshop", "Accepted for testing", HUMAN)
    with zipfile.ZipFile(tmp_path / "releases" / f"{release['id']}.zip") as pack:
        bom = pack.read("BOM.csv").decode()
        readme = pack.read("README.txt").decode()
    assert "Cast acrylic" in bom and "6061" not in bom and "rev B" in readme


def test_replay_change_after_sending_shows_what_was_sent(led, tmp_path):
    deck(led)
    mech.create_release(led, tmp_path, "Workshop", "", HUMAN)
    assert mech.revise(led, "P12", {"material": "Cast acrylic"}, "Budget", HUMAN)["alreadySentIn"] == ["R1"]


def test_replay_late_cost_is_flagged(led):
    mech.set_budget(led, "cost", 30, HUMAN)
    deck(led)
    assert "over the 30 budget" in mech.rollup(led, led.state())["problems"][0]


def test_replay_obsolete_size_in_current_folder_is_found(tmp_path):
    (tmp_path / "3 STEP").mkdir()
    for name in ("P24 Rail angle 10x10x1.5 x 183 - lower.step", "P24 Rail angle 20 x 20 x 1 x 183.step", "P12 Deck.step"):
        (tmp_path / "3 STEP" / name).write_text("x")
    found = importer.gaps(tmp_path)
    assert any("P24" in g["text"] and g["level"] == "Blocks that work" for g in found)
    assert not any("P12" in g["text"] for g in found)


def test_replay_disabled_feature_and_missing_datasheets(tmp_path):
    (tmp_path / "main").mkdir()
    (tmp_path / "main" / "tof.h").write_text("#define TOF_ENABLED             0\n")
    text = " ".join(g["text"] for g in importer.gaps(tmp_path))
    assert "TOF_ENABLED" in text and "no datasheets" in text


def test_replay_deleted_docs_are_flagged(tmp_path):
    (tmp_path / "README.md").write_text("See `Master Spec.md` for detail.")
    assert any("Master Spec.md" in g["text"] for g in importer.gaps(tmp_path))


def test_replay_incomplete_purchase_flags(led):
    proc.add_order(led, {"supplier": "Shop", "total": 13.9}, HUMAN)
    flags = led.state().of_type("order")[0]["fields"]["flags"]
    assert "No currency" in flags and "No order date" in flags and "No buyer" in flags


def test_replay_duties_are_flagged_by_code():
    flags = proc.order_flags({"supplier": "D", "currency": "SGD", "order_date": "x", "total": 1, "buyer": "a", "funding": "b"}, "CPT (Duty, customs, and taxes due at delivery)")
    assert flags == ["Import duties may be due and aren't included"]


# ---- Parts and checks ----


def test_duplicate_part_is_refused(led):
    deck(led)
    with pytest.raises(mech.MechanicalError):
        deck(led)


def test_revision_needs_a_reason_and_a_change(led):
    deck(led)
    with pytest.raises(mech.MechanicalError):
        mech.revise(led, "P12", {"material": "x"}, " ", HUMAN)
    with pytest.raises(mech.MechanicalError, match="Nothing changed"):
        mech.revise(led, "P12", {"material": "Al 6061-T6"}, "r", HUMAN)


def test_closing_a_check_needs_evidence(led):
    deck(led)
    mech.revise(led, "P12", {"material": "Cast acrylic"}, "r", HUMAN)
    with pytest.raises(mech.MechanicalError):
        mech.close_check(led, "P12", "Strength", "", HUMAN)
    mech.close_check(led, "P12", "Strength", "Bending calc", HUMAN)
    assert "Strength" not in {c["check"] for c in mech.open_checks(led)}


def test_agent_cannot_close_a_check_or_set_budget(led):
    deck(led)
    mech.revise(led, "P12", {"material": "Cast acrylic"}, "r", HUMAN)
    with pytest.raises(PermissionDenied):
        mech.close_check(led, "P12", "Strength", "trust me", Actor.agent("electrical"))
    with pytest.raises(PermissionDenied):
        mech.set_budget(led, "cost", 999, Actor.agent("electrical"))


def test_release_blocked_by_open_checks_unless_reason_given(led, tmp_path):
    deck(led)
    mech.revise(led, "P12", {"material": "Cast acrylic"}, "r", HUMAN)
    with pytest.raises(mech.MechanicalError, match="still open"):
        mech.create_release(led, tmp_path, "Workshop", "", HUMAN)
    assert mech.create_release(led, tmp_path, "Workshop", "Prototype only", HUMAN)["id"] == "R1"


def test_rollup_totals_and_missing_values(led):
    mech.add_part(led, {"number": "A", "name": "a", "qty": 2, "mass_g": 10.0, "unit_cost": 1.5}, HUMAN)
    mech.add_part(led, {"number": "B", "name": "b", "qty": 1, "mass_g": None, "unit_cost": None}, HUMAN)
    r = mech.rollup(led, led.state())
    assert r["massG"] == 20 and r["cost"] == 3 and r["missingMass"] == ["B"]


# ---- Geometry on real STEP files ----


@pytest.fixture
def boxes(tmp_path):
    cq = pytest.importorskip("cadquery")
    a = cq.Workplane("XY").box(10, 20, 30, centered=False)
    far = cq.Workplane("XY").transformed(offset=(15, 0, 0)).box(10, 20, 30, centered=False)
    overlap = cq.Workplane("XY").transformed(offset=(5, 0, 0)).box(10, 20, 30, centered=False)
    paths = {}
    for name, shape in (("a", a), ("far", far), ("overlap", overlap)):
        paths[name] = tmp_path / f"{name}.step"
        cq.exporters.export(shape, str(paths[name]))
    return paths


def test_measure_real_step(boxes):
    m = geometry.measure(boxes["a"])
    assert m["sizeMm"] == [10, 20, 30] and m["volumeCm3"] == 6 and m["valid"]


def test_clearance_reports_gap_and_clash(boxes):
    assert geometry.clearance({"a": boxes["a"], "far": boxes["far"]})[0]["gapMm"] == 5
    assert geometry.clearance({"a": boxes["a"], "o": boxes["overlap"]})[0]["status"] == "clash"


# ---- Redaction ----


INVOICE = """Tax Invoice
Ship to: Jane Tan
12 Kent Road
Singapore 119077
Sold By: Good Bearings
Item bearing x2     17.77 SGD
Email jane.tan@example.com
Tel: +65 9123 4567
Paid by Visa ending in 7640
Prepared for Ali Hassan
Grand total 17.77"""


def test_redaction_removes_personal_details_and_keeps_money():
    text = proc.redact(INVOICE, ["Ali Hassan"])
    for secret in ("Jane", "Kent", "119077", "example.com", "9123", "7640", "Ali Hassan"):
        assert secret not in text
    assert "17.77" in text and "Good Bearings" in text and proc.is_clean(text, ["Ali Hassan"])


def test_guard_stops_text_with_leftovers():
    assert not proc.is_clean("contact a@b.co", [])
    assert not proc.is_clean("Visa ending 7640", [])
    assert not proc.is_clean("from Jane", ["Jane"])


@pytest.mark.asyncio
async def test_invoice_with_leftovers_is_never_sent(led):
    class G:
        async def run(self, **_):
            raise AssertionError("must not be called")

    with pytest.raises(proc.ProcurementError):
        await proc.read_invoice(led, G(), "email a@b.co", [])


# ---- Import ----


@pytest.fixture
def project_folder(tmp_path):
    root = tmp_path / "old"
    (root / "docs").mkdir(parents=True)
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.js").write_text("x")
    (root / "README.md").write_text("The robot must carry 3 kg up stairs.\nContact jane@example.com\n")
    (root / "docs" / "model.step").write_text("x")
    return root


def snapshot(root):
    return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def test_scan_counts_and_skips_dependency_folders(project_folder):
    s = importer.scan(project_folder)
    assert s["files"] == 2 and s["counts"]["CAD models and drawings"] == 1 and s["readFirst"] == ["README.md"]


def test_preview_redacts_and_escaping_the_folder_is_refused(project_folder, tmp_path):
    (tmp_path / "secret.txt").write_text("private")
    p = importer.preview(project_folder, ["README.md", "../secret.txt"], [])
    assert len(p["items"]) == 1 and "jane@" not in p["items"][0]["text"] and p["estimatedUsd"] >= 0


@pytest.mark.asyncio
async def test_draft_keeps_only_quoted_items_and_never_touches_originals(led, project_folder):
    class G:
        async def run(self, **_):
            return importer.DraftOut(items=[
                importer.ItemOut(kind="requirement", text="Carry 3 kg", source_file="README.md", quote="must carry 3 kg up stairs"),
                importer.ItemOut(kind="requirement", text="Fly", source_file="README.md", quote="the robot can also fly quickly"),
            ])

    before = snapshot(project_folder)
    result = await importer.draft(led, G(), project_folder, ["README.md"], [])
    assert result == {"created": 1, "rejected": 1}
    assert snapshot(project_folder) == before
    item = led.state().of_type("imported_item")[0]
    assert item["status"] == "proposed" and not led.state().of_type("requirement")


def test_confirming_an_item_creates_a_real_record(led):
    item = led.propose(Actor.agent("importer"), "imported_item", kind="requirement", text="Carry 3 kg", source="R.md", quote="q")
    importer.confirm_item(led, item, HUMAN)
    requirement = led.state().of_type("requirement", "confirmed")[0]
    assert requirement["fields"]["text"] == "Carry 3 kg" and requirement["fields"]["imported_from"] == "R.md"


# ---- API ----


class Provider:
    def resolve(self, tier):
        return TestModel(custom_output_text="ok"), "fake", {}


@pytest.fixture
def client(tmp_path):
    return TestClient(api.create_app(tmp_path / "data", Provider(), None, {"fake": Price(1, 0, 0, 1)}, secrets=prov.MemoryStore()))


def test_part_revise_release_over_api(client):
    pid = client.post("/api/projects", json={"name": "Robot"}).json()["id"]
    base = f"/api/projects/{pid}"
    view = client.post(f"{base}/parts", json={"number": "P1", "name": "Plate", "material": "Al", "mass_g": 5}).json()
    assert view["parts"][0]["current"]["fields"]["revision"] == "A"
    r = client.post(f"{base}/parts/P1/revise", json={"reason": "Cheaper", "material": "Cast acrylic"}).json()
    assert "Strength" in r["impact"]["reopened"]
    assert client.post(f"{base}/releases", json={"recipient": "Shop"}).status_code == 400
    release = client.post(f"{base}/releases", json={"recipient": "Shop", "override": "Prototype"}).json()
    assert client.get(f"{base}/releases/{release['release']['id']}/file").status_code == 200


def test_upload_wrong_type_is_refused(client):
    pid = client.post("/api/projects", json={"name": "Robot"}).json()["id"]
    client.post(f"/api/projects/{pid}/parts", json={"number": "P1", "name": "Plate"})
    assert client.post(f"/api/projects/{pid}/parts/P1/file?filename=x.exe", content=b"x").status_code == 400


def test_import_scan_missing_folder(client):
    assert client.post("/api/import/scan", json={"folder": "Z:/definitely/not/here"}).status_code == 404


def test_resume_marks_earlier_gates_as_imported_not_reviewed(led):
    from buildanything import gates

    gates.resume_at(led, "G3", HUMAN)
    state = led.state()
    assert state.gates_imported == ["G0", "G1", "G2"] and gates.current_gate(state) == "G3"
    with pytest.raises(gates.GateError):
        gates.resume_at(led, "G5", HUMAN)
    with pytest.raises(PermissionDenied):
        gates.resume_at(Ledger(led.root.parent / "new"), "G3", Actor.agent("controller"))
