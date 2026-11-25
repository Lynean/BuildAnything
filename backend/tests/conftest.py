import pytest

from buildanything import headquarters as hq


@pytest.fixture(autouse=True)
def departments_only_work_when_a_test_asks(monkeypatch):
    """Headquarters sending departments to work on a gate has its own tests in test_fixes.py."""
    monkeypatch.setattr(hq, "MAX_DRIVES", 0)
    monkeypatch.setattr(hq, "AUTO_CI", False)
    monkeypatch.setattr(hq, "GATE_REVIEW", False)
    monkeypatch.setattr(hq, "CLONE_STAGGER_S", 0)
