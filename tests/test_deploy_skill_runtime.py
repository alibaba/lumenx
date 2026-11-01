import pytest

from scripts import verify_k8s_fault_drill


def test_k8s_fault_drill_is_non_mutating_without_execute(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["verify_k8s_fault_drill.py"])
    assert verify_k8s_fault_drill.main() == 0
    assert "dry-run" in capsys.readouterr().out
