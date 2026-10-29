"""Prepared image preservation through the real Studio package HTTP routes."""

import hashlib
import io
import json
import zipfile

from fastapi.testclient import TestClient

from tests import test_storyboard_qwen20_core as frame_core


# Reuse the real pipeline and Session.send-only external transport fixtures.
runtime = frame_core.runtime
transport = frame_core.transport


def test_http_package_preserves_prepared_unprojected_frame_image(runtime, transport):
    edited_prompt = "Author changed this prompt while the paid image was rendering"
    frame_core.queue_image(transport)
    edited_during_submit = []

    def change_author_prompt(request):
        if request.method != "POST":
            return
        assert request.url.endswith("/services/aigc/multimodal-generation/generation")
        transport["on_send"] = None
        before = runtime.read_committed_script("p")
        pending = before.asset_image_requests[0].members[0].execution
        assert pending.phase == "submitting"
        assert pending.prepared_artifact is None and pending.artifact is None
        updated = runtime.update_frame(
            "p", "f", expected_revision=before.revision, image_prompt=edited_prompt,
        )
        assert updated.revision > before.revision
        assert updated.frames[0].image_prompt == edited_prompt
        edited_during_submit.append((before.revision, updated.revision))

    transport["on_send"] = change_author_prompt
    app = frame_core.http.real_app(runtime)
    with TestClient(app) as client:
        frame_core.http.enable_fake_key(app)
        raw = frame_core.command(runtime)
        admitted = client.post("/projects/p/asset_image_requests", json=raw)
        assert admitted.status_code == 200, admitted.text
        assert admitted.json()["request"]["members"][0]["status"] == "pending"
        assert len(edited_during_submit) == 1

        # BackgroundTasks has completed the real Core worker before this response
        # returns. The CAS edit prevented product projection, not the paid result.
        source = runtime.read_committed_script("p")
        assert len(source.asset_image_requests) == 1
        record = source.asset_image_requests[0]
        assert record.id == admitted.json()["request"]["id"]
        assert record.client_request_id == raw["client_request_id"]
        assert (record.owner_kind, record.owner_id, record.asset_type, record.asset_id) == (
            "script", "p", "storyboard_frame", "f",
        )
        member = record.members[0]
        execution = member.execution
        assert execution.phase == "submitting" and execution.artifact is None
        proof = execution.prepared_artifact
        assert proof is not None
        output_ref = execution.output_ref
        assert output_ref == f"assets/studio/{execution.attempt_id}.png"
        assert proof["attempt_id"] == member.variant_id == execution.attempt_id
        assert proof["output_ref"] == proof["relative_path"] == output_ref
        paid_bytes = (runtime.data_dir / output_ref).read_bytes()
        assert paid_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(paid_bytes) == proof["size_bytes"]
        assert hashlib.sha256(paid_bytes).hexdigest() == proof["sha256"]

        original_frame = source.frames[0]
        assert original_frame.image_prompt == edited_prompt
        assert original_frame.image_request_id == record.id
        original_asset = original_frame.rendered_image_asset
        assert original_asset is not None
        assert [variant.id for variant in original_asset.variants] == ["old"]
        assert original_asset.selected_id == "old"
        original_variant = original_asset.variants[0]
        assert original_variant.is_favorited and original_variant.prompt_used == "old prompt"
        assert original_frame.image_url == original_frame.rendered_image_url == original_variant.url
        generated_prompt = execution.intent["prompt"]
        assert generated_prompt != edited_prompt and generated_prompt != original_variant.prompt_used
        assert transport["queue"] == []
        assert [request.method for request, _ in transport["calls"]] == ["POST", "GET"]
        provider_before = [
            (request.method, request.url, request.body, dict(request.headers))
            for request, _ in transport["calls"]
        ]
        source_business = json.loads((runtime.data_dir / "projects.json").read_bytes())["p"]
        source_media = {
            ref: (runtime.data_dir / ref).read_bytes()
            for ref in (original_variant.url, output_ref)
        }

        def assert_source_unchanged():
            assert json.loads((runtime.data_dir / "projects.json").read_bytes())["p"] == source_business
            assert runtime.read_committed_script("p").model_dump(mode="json") == source.model_dump(mode="json")
            assert {ref: (runtime.data_dir / ref).read_bytes() for ref in source_media} == source_media
            assert [
                (request.method, request.url, request.body, dict(request.headers))
                for request, _ in transport["calls"]
            ] == provider_before
            assert transport["queue"] == []

        # Export is read-only: compare all three DB files before the first
        # preservation assertion, so the initial RED still proves this boundary.
        databases_before_export = frame_core.persisted(runtime)
        exported = client.get("/projects/p/package/export")
        assert exported.status_code == 200, exported.text
        assert frame_core.persisted(runtime) == databases_before_export
        assert_source_unchanged()
        with zipfile.ZipFile(io.BytesIO(exported.content)) as package:
            paid_entry = "media/" + output_ref
            assert paid_entry in package.namelist(), (
                "Paid prepared PNG was omitted because it has no product variant"
            )
            assert package.read(paid_entry) == paid_bytes
            manifest = json.loads(package.read("manifest.json"))
            media = [item for item in manifest["media"] if item["logical_ref"] == output_ref]
            assert len(media) == 1
            assert media[0]["package_path"] == paid_entry
            assert media[0]["sha256"] == proof["sha256"]
            assert media[0]["size_bytes"] == proof["size_bytes"]
            payload = json.loads(package.read("project.json"))

        # These complete round-trip assertions are intentionally after the first
        # missing-media assertion. An initial RED does not validate this suffix.
        packaged = payload["script"]
        packaged_frame = packaged["frames"][0]
        packaged_asset = packaged_frame["rendered_image_asset"]
        assert packaged_frame["image_prompt"] == edited_prompt
        assert packaged_asset["selected_id"] == "old"
        assert len(packaged_asset["variants"]) == 2
        packaged_new = next(v for v in packaged_asset["variants"] if v["id"] == member.variant_id)
        assert packaged_new["url"] == output_ref and packaged_new["prompt_used"] == generated_prompt
        assert packaged_frame.get("image_request_id") is None
        assert packaged.get("asset_image_request_scope_id") is None
        assert packaged["asset_image_requests"] == []
        assert packaged["provider_request_receipts"] == []
        assert payload["resolved_assets"]["asset_image_requests"] == []

        imported = client.post(
            "/projects/package/import", content=exported.content,
            headers={"content-type": "application/zip"},
        )
        assert imported.status_code == 200, imported.text
        copied_id = imported.json()["id"]
        assert copied_id != "p"
        # Import legitimately changes the projects map. Do not compare the whole
        # projects.json to its pre-import bytes; protect the original project.
        assert_source_unchanged()
        copied = runtime.read_committed_script(copied_id)
        assert copied.asset_image_request_scope_id is None
        assert copied.asset_image_requests == [] and copied.provider_request_receipts == []
        assert copied.video_tasks == []
        assert len(copied.frames) == 1
        copied_frame = copied.frames[0]
        assert copied_frame.id != "f" and copied_frame.image_request_id is None
        assert copied_frame.status == "completed"
        assert copied_frame.image_prompt == edited_prompt
        copied_asset = copied_frame.rendered_image_asset
        assert copied_asset is not None and len(copied_asset.variants) == 2
        copied_old = next(v for v in copied_asset.variants if v.prompt_used == "old prompt")
        copied_new = next(v for v in copied_asset.variants if v.prompt_used == generated_prompt)
        assert copied_old.id != "old" and copied_new.id != member.variant_id
        assert copied_old.is_favorited and copied_asset.selected_id == copied_old.id
        assert copied_asset.selected_id != copied_new.id
        assert copied_frame.image_url == copied_frame.rendered_image_url == copied_old.url
        assert (runtime.data_dir / copied_old.url).read_bytes() == source_media[original_variant.url]
        copied_bytes = (runtime.data_dir / copied_new.url).read_bytes()
        assert copied_bytes == paid_bytes
        assert hashlib.sha256(copied_bytes).hexdigest() == proof["sha256"]
        assert len(copied_bytes) == proof["size_bytes"]

        databases_before_reexport = frame_core.persisted(runtime)
        reexported = client.get(f"/projects/{copied_id}/package/export")
        assert reexported.status_code == 200, reexported.text
        assert frame_core.persisted(runtime) == databases_before_reexport
        assert_source_unchanged()
        with zipfile.ZipFile(io.BytesIO(reexported.content)) as package:
            assert package.read("media/" + copied_new.url) == paid_bytes
            manifest = json.loads(package.read("manifest.json"))
            media = [item for item in manifest["media"] if item["logical_ref"] == copied_new.url]
            assert len(media) == 1
            assert (media[0]["sha256"], media[0]["size_bytes"]) == (proof["sha256"], proof["size_bytes"])
            payload = json.loads(package.read("project.json"))
        reexported_frame = payload["script"]["frames"][0]
        assert reexported_frame["image_prompt"] == edited_prompt
        assert reexported_frame["rendered_image_asset"]["selected_id"] == copied_old.id
        assert len(reexported_frame["rendered_image_asset"]["variants"]) == 2
        assert reexported_frame.get("image_request_id") is None
        assert payload["script"].get("asset_image_request_scope_id") is None
        assert payload["script"]["asset_image_requests"] == []
        assert payload["script"]["provider_request_receipts"] == []
        assert_source_unchanged()
