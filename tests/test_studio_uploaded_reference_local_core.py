"""An OSS mirror must not erase the managed source of a Studio upload."""

import base64
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from tests import test_studio_core_image_admission as admission
from tests import test_studio_full_body_reference_core as full_body
from tests import test_studio_gpt_reference_sheet_core as gpt
from tests import test_studio_three_view_core as three_view
from tests.frozen_image_fixtures import rgb_png_bytes

runtime = three_view.runtime
transport = gpt.transport


def test_mirrored_upload_stays_local_and_flows_into_existing_gpt_full_body_core(runtime, transport, monkeypatch):
    admission.setup(runtime, "local")
    project = runtime.scripts["p"]
    project.model_settings.t2i_model = "wan2.7-image"
    project.model_settings.i2i_model = "gpt-image-2"
    runtime._commit_storage("projects.json")
    source = rgb_png_bytes("uploaded three-view source", width=16, height=16)
    uploads = []
    uploader = Mock(is_configured=True, base_path="lumenx")

    def mirror(path):
        assert Path(path).read_bytes() == source
        uploads.append(Path(path))
        return "lumenx/assets/characters/oss-mirror.png"

    uploader.upload_image.side_effect = mirror
    uploader.sign_url_for_display.side_effect = lambda ref: f"https://signed.example.invalid/{ref}"
    monkeypatch.setattr(api, "OSSImageUploader", Mock(return_value=uploader))
    app = three_view.app_for(runtime)
    with TestClient(app) as client:
        gpt.configure(app, "http")
        uploaded = client.post(
            "/projects/p/assets/character/c/upload", params={"upload_type": "three_views"},
            files={"file": ("three-views.png", source, "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        wire = uploaded.json()["characters"][0]
        saved = runtime.read_committed_script("p").characters[0]
        assert saved.three_view_asset is not None and saved.three_views is not None
        reference = saved.three_view_asset.variants[0]
        assert reference.url.startswith("uploads/"), "OSS success replaced the managed reference with an unversioned key"
        assert reference.is_uploaded_source and reference.upload_type == "three_views"
        assert saved.three_views.image_variants[0].url == reference.url
        assert wire["three_view_asset"]["variants"][0]["url"] == reference.url
        assert wire["three_views"]["image_variants"][0]["url"] == reference.url
        assert uploads == [runtime.data_dir / reference.url]
        assert (runtime.data_dir / reference.url).read_bytes() == source
        assert not transport["calls"]

        gpt.queue_images(transport, "http", count=1)
        generated = client.post(full_body.OLD_ROUTE, json={
            "asset_id": "c", "asset_type": "character", "generation_type": "full_body",
            "model_name": "wan2.7-image", "prompt": full_body.PROMPT,
            "apply_style": False, "negative_prompt": "", "batch_size": 1,
        })
        assert generated.status_code == 200, generated.text
        completed = runtime.read_committed_script("p")
        assert len(completed.asset_image_requests) == 1
        record = completed.asset_image_requests[0]
        assert record.channel == "full_body" and record.entrypoint == full_body.ENTRYPOINT
        execution = record.members[0].execution
        assert execution.phase == "completed"
        assert execution.intent["intent_contract_version"] == full_body.CONTRACT
        assert len(execution.media) == 1
        assert execution.media[0]["sha256"] == hashlib.sha256(source).hexdigest()
        assert execution.media[0]["size_bytes"] == len(source)
        posts = [request for request, _ in transport["calls"] if request.method == "POST"]
        assert len(posts) == 1 and posts[0].url == three_view.EDIT
        payload = json.loads(posts[0].body)
        assert payload["images"] == ["data:image/png;base64," + base64.b64encode(source).decode()]
        assert payload["prompt"].startswith(full_body.REVERSE)
        final_source = completed.characters[0].three_view_asset
        assert final_source is not None and final_source.variants[0].url == reference.url
        assert uploader.upload_image.call_count == 1
        assert not transport["queue"] and not transport["cli"]
