"""The canvas and service must resolve the same take without silent substitution."""

import json
from pathlib import Path

import pytest

from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.reference_commands import AttachReferenceCommand
from src.apps.atelier.service import AtelierService
from src.apps.atelier.storage import AtelierStorage


CASES = json.loads((Path(__file__).resolve().parents[1] / "frontend/src/__tests__/fixtures/atelier-reference-resolution.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_reference_resolution_matches_canvas_contract(tmp_path, case):
    storage = AtelierStorage(tmp_path)
    try:
        storage.create_project(AtelierProject(id="p", title="References", created_at=1, updated_at=1, nodes=[
            AtelierNode(id="target", project_id="p", type="video"),
            AtelierNode(id="source", project_id="p", type="video", media_urls=["video/node.mp4"], data=case["data"]),
        ]))
        service = AtelierService(storage)
        command = AttachReferenceCommand(expected_revision=0, source_node_id="source")
        if case["expected"] is None:
            with pytest.raises(ValueError):
                service.attach_reference("p", "target", command)
            assert service.get_project("p").revision == 0
        else:
            ack = service.attach_reference("p", "target", command)
            assert ack.project.nodes[0].data["reference_video_urls"] == [case["expected"]]
    finally:
        storage.close()
