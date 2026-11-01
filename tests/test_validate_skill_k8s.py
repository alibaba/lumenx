import pytest

from scripts import validate_skill_k8s


def test_k8s_template_safety_gate_passes(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["validate_skill_k8s.py"])
    assert validate_skill_k8s.main() == 0
    assert "PASS" in capsys.readouterr().out


def test_k8s_production_gate_requires_real_image_inputs(monkeypatch):
    monkeypatch.setattr("sys.argv", ["validate_skill_k8s.py", "--production"])
    with pytest.raises(SystemExit, match="image-prefix"):
        validate_skill_k8s.main()
