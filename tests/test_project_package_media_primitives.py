import hashlib
import io
import zipfile

import pytest

from src.utils.json_storage import DirectoryWriterLock


def test_neutral_media_ref_normalization_reuses_explicit_policy():
    from src.core.project_package_media import MediaRootPolicy, ProjectPackageMediaError, normalize_media_ref

    policy = MediaRootPolicy(allowed_roots=frozenset({"uploads", "video"}), ref_prefixes=("/files/", "output/"))
    assert normalize_media_ref("/files/uploads/a.png", policy=policy) == "uploads/a.png"
    assert normalize_media_ref("output/video/a.mp4", policy=policy) == "video/a.mp4"
    with pytest.raises(ProjectPackageMediaError, match="Remote media"):
        normalize_media_ref("https://example.com/a.png", policy=policy)
    with pytest.raises(ProjectPackageMediaError, match="Unsupported media path"):
        normalize_media_ref("assets/a.png", policy=policy)


def test_neutral_strict_json_and_zip_validation_reject_unsafe_payloads():
    from src.core.project_package_media import ProjectPackageMediaError, loads_strict, validate_zip_infos

    with pytest.raises(ProjectPackageMediaError, match="Duplicate JSON key"):
        loads_strict(b'{"a":1,"a":2}', "project.json")

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("../evil", b"x")
    with zipfile.ZipFile(io.BytesIO(payload.getvalue())) as zf:
        with pytest.raises(ProjectPackageMediaError, match="Unsafe package path"):
            validate_zip_infos(
                zf,
                metadata_entries={"manifest.json"},
                media_entry_prefix="media/",
                max_files=4,
                max_file_bytes=1024,
                max_package_bytes=4096,
                max_compression_ratio=100,
            )


def test_neutral_stage_media_uses_pinned_writer_lock_and_cleans_own_files(tmp_path):
    from src.core.project_package_media import MediaRootPolicy, cleanup_imported_media, stage_media

    data = b"video-bytes"
    entry = {
        "logical_ref": "video/source.mp4",
        "package_path": "media/video/source.mp4",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }
    media_map = {"video/source.mp4": "video/imported/project-new/source.mp4"}
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr(entry["package_path"], data)

    policy = MediaRootPolicy(allowed_roots=frozenset({"video"}), ref_prefixes=())
    root = tmp_path / "target"
    with DirectoryWriterLock(root).acquire() as lease:
        with zipfile.ZipFile(io.BytesIO(package.getvalue())) as zf:
            stage_media(zf, [entry], media_map=media_map, data_root=root, writer_lock=lease, policy=policy)
        written = root / media_map["video/source.mp4"]
        assert written.read_bytes() == data
        unrelated = root / "video" / "another-project.mp4"
        unrelated.write_bytes(b"unrelated")
        cleanup_imported_media(
            [entry],
            media_map=media_map,
            import_project_id="project-new",
            data_root=root,
            writer_lock=lease,
            policy=policy,
        )
        assert not written.exists()
        assert unrelated.read_bytes() == b"unrelated"
