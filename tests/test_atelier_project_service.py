from __future__ import annotations

import pytest

from src.apps.atelier.service import (
    AtelierService,
    SourceProjectNotFound,
    _OMITTED,
)
from src.apps.atelier.storage import AtelierProjectNotFound, AtelierStorage
from src.utils.json_storage import StorageConflictError, StorageError


class Sequencer:
    def __init__(self):
        self.now = 100.0
        self.ids = 0

    def clock(self) -> float:
        self.now += 1.0
        return self.now

    def id_factory(self) -> str:
        self.ids += 1
        return f"atelier-{self.ids}"


def make_service(
    tmp_path, *, sources: set[str] | None = None
) -> tuple[AtelierService, AtelierStorage, Sequencer]:
    seq = Sequencer()
    storage = AtelierStorage(tmp_path)
    service = AtelierService(
        storage,
        source_project_exists=(lambda source_id: source_id in (sources or set())),
        clock=seq.clock,
        id_factory=seq.id_factory,
    )
    return service, storage, seq


def test_create_project_defaults_untrusted_and_returns_committed_snapshot(tmp_path):
    service, storage, _seq = make_service(tmp_path)

    created = service.create_project(title="First", description="")
    created.title = "mutated client copy"
    current = storage.get_project("atelier-1")

    assert current.title == "First"
    assert current.description == ""
    assert current.source_project_id is None
    assert current.agent_policy.approval_mode.value == "untrusted"
    assert current.revision == 0
    assert current.created_at == current.updated_at


def test_create_project_validates_source_without_rewriting_nonempty_id(tmp_path):
    service, storage, _seq = make_service(tmp_path, sources={"studio-1"})

    created = service.create_project(title="First", source_project_id="studio-1")
    assert created.source_project_id == "studio-1"

    with pytest.raises(SourceProjectNotFound):
        service.create_project(title="Missing", source_project_id="missing")
    with pytest.raises(ValueError, match="non-empty"):
        service.create_project(title="Blank", source_project_id="   ")
    assert [project.id for project in storage.list_projects()] == ["atelier-1"]


def test_create_project_distinguishes_omitted_source_from_explicit_null(tmp_path):
    calls: list[str | None] = []
    seq = Sequencer()
    storage = AtelierStorage(tmp_path)
    service = AtelierService(
        storage,
        source_project_exists=lambda source_id: calls.append(source_id) or True,
        clock=seq.clock,
        id_factory=seq.id_factory,
    )

    omitted = service.create_project(title="Standalone")
    explicit_null = service.create_project(title="Explicit", source_project_id=None)

    assert omitted.source_project_id is None
    assert explicit_null.source_project_id is None
    assert calls == []


def test_source_project_requires_injected_existence_authority(tmp_path):
    seq = Sequencer()
    storage = AtelierStorage(tmp_path)
    service = AtelierService(storage, clock=seq.clock, id_factory=seq.id_factory)

    with pytest.raises(SourceProjectNotFound):
        service.create_project(title="With source", source_project_id="studio-1")

    standalone = service.create_project(title="Standalone")
    assert standalone.source_project_id is None
    explicit_null = service.create_project(
        title="Explicit null", source_project_id=None
    )
    assert explicit_null.source_project_id is None

    with pytest.raises(SourceProjectNotFound):
        service.update_project(
            standalone.id, expected_revision=0, source_project_id="studio-1"
        )


def test_update_project_patch_semantics_revision_and_null_source(tmp_path):
    service, _storage, _seq = make_service(tmp_path, sources={"studio-1"})
    created = service.create_project(
        title="First", description="Desc", source_project_id="studio-1"
    )

    unchanged = service.update_project(
        created.id,
        expected_revision=0,
        title=_OMITTED,
        description=_OMITTED,
        source_project_id=_OMITTED,
    )
    assert unchanged.revision == 0
    assert unchanged.updated_at == created.updated_at

    changed = service.update_project(
        created.id,
        expected_revision=0,
        title="Second",
        description="Next",
        source_project_id=None,
    )
    assert changed.title == "Second"
    assert changed.description == "Next"
    assert changed.source_project_id is None
    assert changed.revision == 1
    assert changed.updated_at > created.updated_at

    with pytest.raises(StorageConflictError):
        service.update_project(created.id, expected_revision=0, title="Late")


def test_update_rejects_null_title_description_and_bool_revision(tmp_path):
    service, _storage, _seq = make_service(tmp_path)
    created = service.create_project(title="First")

    with pytest.raises(ValueError, match="title"):
        service.update_project(created.id, expected_revision=0, title=None)
    with pytest.raises(ValueError, match="description"):
        service.update_project(created.id, expected_revision=0, description=None)
    with pytest.raises(StorageConflictError):
        service.update_project(created.id, expected_revision=True, title="Bool")
    with pytest.raises(StorageConflictError):
        service.delete_project(created.id, expected_revision=True)


def test_delete_and_notfound_are_typed(tmp_path):
    service, storage, _seq = make_service(tmp_path)
    created = service.create_project(title="First")

    service.delete_project(created.id, expected_revision=0)
    with pytest.raises(AtelierProjectNotFound):
        service.get_project(created.id)
    assert storage.list_projects() == []


def test_storage_failure_propagates_without_fake_success(tmp_path, monkeypatch):
    service, storage, _seq = make_service(tmp_path)
    created = service.create_project(title="First")

    def fail_commit(_next_projects):
        raise StorageError("disk unavailable")

    monkeypatch.setattr(storage, "_commit", fail_commit)
    with pytest.raises(StorageError):
        service.update_project(created.id, expected_revision=0, title="Lost")
    assert storage.get_project(created.id).title == "First"


def test_create_source_disappearing_after_service_prevalidation_does_not_commit(
    tmp_path, monkeypatch
):
    sources = {"studio-1"}
    service, storage, _seq = make_service(tmp_path, sources=sources)
    original_create = storage.create_project

    def remove_source_then_create(project, **kwargs):
        sources.clear()
        return original_create(project, **kwargs)

    monkeypatch.setattr(storage, "create_project", remove_source_then_create)

    with pytest.raises(SourceProjectNotFound):
        service.create_project(title="Race", source_project_id="studio-1")
    assert storage.list_projects() == []


def test_update_source_disappearing_after_service_prevalidation_does_not_publish_revision(
    tmp_path, monkeypatch
):
    sources = {"studio-1", "studio-2"}
    service, storage, _seq = make_service(tmp_path, sources=sources)
    created = service.create_project(title="First", source_project_id="studio-1")
    original_apply = storage.apply_project_command

    def remove_source_then_apply(*args, **kwargs):
        sources.discard("studio-2")
        return original_apply(*args, **kwargs)

    monkeypatch.setattr(storage, "apply_project_command", remove_source_then_apply)

    with pytest.raises(SourceProjectNotFound):
        service.update_project(
            created.id, expected_revision=0, source_project_id="studio-2"
        )
    unchanged = storage.get_project(created.id)
    assert unchanged.source_project_id == "studio-1"
    assert unchanged.revision == 0
