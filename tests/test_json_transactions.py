"""Crash consistency for associated JSON records; only synthetic temp data."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.utils.json_storage import StorageError, CorruptStorageError
from src.utils.json_transactions import JsonFileTransaction


NAMES = ("projects.json", "series.json")


def make_store(tmp_path):
    for name in NAMES:
        (tmp_path / name).write_text('{"old": true}\n')
    return JsonFileTransaction(tmp_path, NAMES)


def test_commit_updates_both_files_and_preserves_initial_backups(tmp_path):
    store = make_store(tmp_path)
    store.commit({name: {"new": name} for name in NAMES})
    for name in NAMES:
        assert json.loads((tmp_path / name).read_text()) == {"new": name}
        assert (tmp_path / '.storage-backups-v1' / name).read_text() == '{"old": true}\n'
    store.commit({NAMES[0]: {"newer": True}})
    assert json.loads((tmp_path / '.storage-backups-v1' / NAMES[0]).read_text()) == {"old": True}
    assert not store.journal_path.exists()


def test_second_file_failure_restores_exact_old_files(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    replace = os.replace
    failed = False

    def fail_once(src, dst):
        nonlocal failed
        if Path(dst) == tmp_path / NAMES[1] and not failed:
            failed = True
            raise OSError('disk failure')
        return replace(src, dst)

    monkeypatch.setattr(os, 'replace', fail_once)
    with pytest.raises(StorageError):
        store.commit({name: {"new": True} for name in NAMES})
    assert [(tmp_path / name).read_text() for name in NAMES] == ['{"old": true}\n'] * 2
    assert not store.journal_path.exists()


def test_serialization_fails_before_any_write(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(StorageError):
        store.commit({NAMES[0]: {"new": 1}, NAMES[1]: object()})
    assert [(tmp_path / name).read_text() for name in NAMES] == ['{"old": true}\n'] * 2
    assert not store.journal_path.exists()


def test_invalid_journal_is_preserved_and_blocks_recovery(tmp_path):
    store = make_store(tmp_path)
    store.journal_path.write_text('{"version": 99}')
    with pytest.raises(CorruptStorageError):
        store.recover()
    assert store.journal_path.read_text() == '{"version": 99}'
    assert (tmp_path / NAMES[0]).read_text() == '{"old": true}\n'


def test_interrupted_process_rolls_back_before_new_read(tmp_path):
    store = make_store(tmp_path)
    probe = tmp_path / 'crash.py'
    probe.write_text('''import os, sys
from pathlib import Path
from src.utils.json_transactions import JsonFileTransaction
import src.utils.json_transactions as module
root = Path(sys.argv[1])
original = module.atomic_write_json
def interrupted(path, value):
    original(path, value)
    if Path(path).name == "projects.json":
        os._exit(23)
module.atomic_write_json = interrupted
JsonFileTransaction(root, ("projects.json", "series.json")).commit({"projects.json": {"new": 1}, "series.json": {"new": 1}})
''')
    helper = Path(os.environ['LUMENX_REPO_ROOT']) / 'scripts/test_support/isolated_child.py'
    result = subprocess.run([sys.executable, '-S', str(helper), str(probe), str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 23, result.stdout + result.stderr
    assert json.loads((tmp_path / NAMES[0]).read_text()) == {"new": 1}
    store.recover()
    assert [(tmp_path / name).read_text() for name in NAMES] == ['{"old": true}\n'] * 2


def test_recovery_rejects_unowned_file_before_touching_data(tmp_path):
    store = make_store(tmp_path)
    store.journal_path.write_text(json.dumps({
        'version': 1, 'phase': 'prepared', 'before': {NAMES[0]: None, '../other.json': None},
    }))
    with pytest.raises(CorruptStorageError):
        store.recover()
    assert (tmp_path / NAMES[0]).exists()


def test_recovery_failure_keeps_journal_for_next_startup(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.journal_path.write_text(json.dumps({'version': 1, 'phase': 'prepared', 'before': {NAMES[0]: '{"old":1}'}}))
    original = os.replace
    with monkeypatch.context() as fault:
        fault.setattr(os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('still full')))
        with pytest.raises(StorageError):
            store.recover()
    assert store.journal_path.exists()
    assert os.replace is original
    store.recover()
    assert json.loads((tmp_path / NAMES[0]).read_text()) == {"old": 1}


def test_committed_journal_does_not_undo_success(tmp_path):
    store = make_store(tmp_path)
    store.journal_path.write_text(json.dumps({'version': 1, 'phase': 'committed', 'before': {NAMES[0]: None}}))
    store.recover()
    assert (tmp_path / NAMES[0]).exists()


def test_target_symlink_is_rejected_without_touching_it(tmp_path):
    store = make_store(tmp_path)
    other = tmp_path / 'other.json'
    other.write_text('{"private":true}')
    (tmp_path / NAMES[0]).unlink()
    (tmp_path / NAMES[0]).symlink_to(other)
    with pytest.raises(StorageError):
        store.commit({NAMES[0]: {"new": 1}})
    assert other.read_text() == '{"private":true}'


@pytest.mark.parametrize('kind', ['directory', 'corrupt'])
def test_invalid_existing_backup_prevents_new_commit(tmp_path, kind):
    store = make_store(tmp_path)
    backup = tmp_path / '.storage-backups-v1' / NAMES[0]
    backup.parent.mkdir()
    if kind == 'directory':
        backup.mkdir()
    else:
        backup.write_text('{')
    with pytest.raises(StorageError):
        store.commit({NAMES[0]: {'new': 1}})
    assert (tmp_path / NAMES[0]).read_text() == '{"old": true}\n'


def test_excessively_nested_before_image_is_corrupt_journal(tmp_path):
    store = make_store(tmp_path)
    store.journal_path.write_text(json.dumps({'version': 1, 'phase': 'prepared', 'before': {
        NAMES[0]: '[' * 2000 + '0' + ']' * 2000,
    }}))
    with pytest.raises(CorruptStorageError):
        store.recover()


@pytest.mark.parametrize('phase,after_replace', [('prepared', True), ('committed', False), ('committed', True)])
def test_journal_failure_distinguishes_uncertain_commit(tmp_path, monkeypatch, phase, after_replace):
    import src.utils.json_transactions as module
    store = make_store(tmp_path)
    write = module.atomic_write_json
    failed = False

    def fault(path, value):
        nonlocal failed
        if Path(path) == store.journal_path and value['phase'] == phase and not failed:
            failed = True
            if after_replace:
                write(path, value)
            raise StorageError('journal fault', commit_uncertain=after_replace)
        return write(path, value)

    monkeypatch.setattr(module, 'atomic_write_json', fault)
    with pytest.raises(StorageError) as error:
        store.commit({name: {'new': 1} for name in NAMES})
    uncertain = phase == 'committed' and after_replace
    assert error.value.commit_uncertain is uncertain
    expected = {'new': 1} if uncertain else {'old': True}
    assert [json.loads((tmp_path / name).read_text()) for name in NAMES] == [expected] * 2
    if uncertain:
        assert store.journal_path.exists()
        with pytest.raises(StorageError, match='unavailable'):
            store.commit({NAMES[0]: {'retry': 1}})
        JsonFileTransaction(tmp_path, NAMES).recover()
        assert json.loads((tmp_path / NAMES[0]).read_text()) == {'new': 1}


def test_failed_rollback_blocks_writes_and_retains_recovery_record(tmp_path, monkeypatch):
    import src.utils.json_transactions as module
    store = make_store(tmp_path)
    write = module.atomic_write_json

    def fault(path, value):
        if Path(path).name == NAMES[1]:
            raise StorageError('write failure')
        return write(path, value)

    with monkeypatch.context() as failure:
        failure.setattr(module, 'atomic_write_json', fault)
        failure.setattr(module, 'atomic_write_text', lambda *_: (_ for _ in ()).throw(StorageError('rollback failure')))
        # Backups must exist first so the injected text failure hits recovery.
        backup = tmp_path / '.storage-backups-v1'
        backup.mkdir()
        for name in NAMES:
            (backup / name).write_text('{"old":true}')
        with pytest.raises(StorageError) as error:
            store.commit({name: {'new': 1} for name in NAMES})
    assert error.value.commit_uncertain
    assert store.journal_path.exists()
    with pytest.raises(StorageError, match='unavailable'):
        store.commit({NAMES[0]: {'retry': 1}})
    JsonFileTransaction(tmp_path, NAMES).recover()
    assert json.loads((tmp_path / NAMES[0]).read_text()) == {'old': True}


def test_committed_cleanup_failure_is_not_reported_as_failed_save(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    monkeypatch.setattr(store, '_remove_journal', lambda: (_ for _ in ()).throw(OSError('cleanup failure')))
    store.commit({name: {'new': 1} for name in NAMES})
    assert store.journal_path.exists()
    assert json.loads(store.journal_path.read_text())['phase'] == 'committed'
    JsonFileTransaction(tmp_path, NAMES).recover()
    assert json.loads((tmp_path / NAMES[0]).read_text()) == {'new': 1}
