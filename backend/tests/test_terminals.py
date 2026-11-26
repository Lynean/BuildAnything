import time

import pytest

from buildanything import terminals


def wait_for(t, text, after=0, seconds=15):
    end, seen = time.time() + seconds, ""
    while time.time() < end:
        out = t.output(after)
        seen += out["text"]
        after = out["seq"]
        if text in seen:
            return seen
        time.sleep(0.2)
    raise AssertionError(f"{text!r} never appeared in: {seen[-500:]!r}")


def test_a_terminal_runs_commands_in_the_project_and_stops_cleanly(tmp_path):
    (tmp_path / "proj" / "firmware" / "app").mkdir(parents=True)
    box = terminals.Terminals()
    try:
        t = box.create("proj", tmp_path / "proj", "firmware/app")
        assert t.view()["folder"] == "firmware/app" and t.view()["running"]
        t.send("echo hello-from-terminal")
        wait_for(t, "hello-from-terminal")
        seq = t.output()["seq"]
        t.stop()
        assert t.view()["running"]  # a fresh shell, same folder
        t.send("echo after-stop")
        wait_for(t, "after-stop", after=seq)
        with pytest.raises(terminals.TerminalError):
            box.create("proj", tmp_path / "proj", "../outside")
        assert [x["id"] for x in box.listing("proj")] == [t.id]
    finally:
        box.close_all()
    assert box.listing() == []
