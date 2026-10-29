from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from src.utils.json_storage import StorageError
from src.utils.runtime_environment import RuntimeEnvironment


def test_snapshot_and_clients_are_owned_by_one_runtime():
    source = {"DASHSCOPE_API_KEY": "first-example"}
    first, second = RuntimeEnvironment(source), RuntimeEnvironment({})
    source["DASHSCOPE_API_KEY"] = "ambient-change"
    old = first.snapshot()
    create = Mock(side_effect=lambda env: {"captured": env})
    client = first.client("llm", create)
    assert client["captured"] is old
    assert first.client("llm", create) is client
    assert second.client("llm", create) is not client
    with pytest.raises(TypeError):
        old["DASHSCOPE_API_KEY"] = "changed"
    persist = Mock()
    first.update({"DASHSCOPE_API_KEY": "updated-example"}, persist=persist)
    assert old["DASHSCOPE_API_KEY"] == "first-example"
    assert first.client("llm", create)["captured"]["DASHSCOPE_API_KEY"] == "updated-example"
    assert dict(second.snapshot()) == {}
    persist.assert_called_once_with({"DASHSCOPE_API_KEY": "updated-example"}, ())


@pytest.mark.parametrize("uncertain", [False, True])
def test_failed_save_cannot_publish_settings_or_retire_clients(uncertain):
    runtime = RuntimeEnvironment({"key": "old", "endpoint": "original"})
    client = runtime.client("model", lambda _: object())
    before = runtime.snapshot()
    persist = Mock(side_effect=StorageError("injected", commit_uncertain=uncertain))
    with pytest.raises(StorageError):
        runtime.update({"key": "new"}, remove_keys=("endpoint",), persist=persist)
    assert runtime.snapshot() is before
    assert runtime.client("model", lambda _: object()) is client


def test_concurrent_updates_preserve_each_field_and_remove_only_named_keys():
    runtime = RuntimeEnvironment({"endpoint": "old", "unrelated": "keep"})
    saved = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(runtime.update, {key: key}, persist=lambda *args: saved.append(args)) for key in ("A", "B")]
        for future in futures:
            future.result(timeout=2)
    runtime.update({}, remove_keys=("endpoint",), persist=lambda *args: saved.append(args))
    assert dict(runtime.snapshot()) == {"unrelated": "keep", "A": "A", "B": "B"}
    assert len(saved) == 3


@pytest.mark.parametrize("invalid", [{"bad": 1}, {"bad": "\0"}, {"bad": "\ud800"}, {"bad=key": "value"}, {"": "value"}])
def test_invalid_configuration_is_rejected_before_persistence(invalid):
    runtime = RuntimeEnvironment({})
    persist = Mock()
    with pytest.raises(StorageError):
        runtime.update(invalid, persist=persist)
    persist.assert_not_called()
    assert dict(runtime.snapshot()) == {}
