"""Regression tests for the isolated pytest runner itself."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import isolation_guard

@pytest.fixture(autouse=True)
def assert_guard_clean_around_test():
    assert isolation_guard.violations() == ()
    yield
    assert isolation_guard.violations() == ()



_MEDIA_FORMATS = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,lavfi,image2,png_pipe,jpeg_pipe,webp_pipe,bmp_pipe,gif,mp3,wav,ogg,flac,aac"
_MEDIA_FORMATS_WITH_CONCAT = f"{_MEDIA_FORMATS},concat"


def _limits(formats: str = _MEDIA_FORMATS) -> list[str]:
    return ["-protocol_whitelist", "file", "-format_whitelist", formats]


def _input(path: object, formats: str = _MEDIA_FORMATS) -> list[str]:
    return [*_limits(formats), "-i", str(path)]


def _capture_real_popen(monkeypatch):
    captured = {}

    class FakeProcess:
        returncode = 0

        def __init__(self, args):
            self.args = args

        def communicate(self, input=None, timeout=None):
            return b'', b''

        def kill(self):
            return None

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_popen(args, *popen_args, **kwargs):
        captured['args'] = list(args)
        return FakeProcess(args)

    monkeypatch.setattr(isolation_guard, '_REAL_POPEN', fake_popen)
    return captured

def _sentinel_file() -> Path:
    value = os.environ.get("LUMENX_TEST_SENTINEL_FILE")
    assert value, "runner must provide an owned sentinel outside the isolation root"
    path = Path(value)
    assert path.exists()
    return path


def test_guard_blocks_owned_outside_sentinel_reads_even_when_caught():
    sentinel = _sentinel_file()
    with pytest.raises(RuntimeError, match="blocked read outside allowed roots"):
        open(sentinel, "r", encoding="utf-8")
    assert isolation_guard.violations()
    isolation_guard.clear_violations()


def test_guard_blocks_writes_outside_temp_root(tmp_path):
    outside = _sentinel_file().parent / "should-not-exist.txt"
    with pytest.raises(RuntimeError, match="blocked write outside isolated root"):
        outside.write_text("bad", encoding="utf-8")
    assert not outside.exists()
    isolation_guard.clear_violations()


def test_guard_blocks_network_attempts():
    with pytest.raises(RuntimeError, match="socket.*blocked"):
        socket.create_connection(("198.51.100.1", 443), timeout=0.01)
    isolation_guard.clear_violations()


def test_guard_blocks_download_or_shell_subprocess():
    with pytest.raises(RuntimeError, match="non-allowlisted command"):
        subprocess.run(["curl", "https://example.invalid"], check=False)
    isolation_guard.clear_violations()


def test_guard_blocks_python_child_without_bootstrap_even_with_isolated_env(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("print('unguarded-child')\n", encoding="utf-8")
    child_env = os.environ.copy()
    child_env["LUMENX_TEST_ISOLATED"] = "1"
    with pytest.raises(RuntimeError, match="Python subprocess.*isolated_child"):
        subprocess.run([sys.executable, str(probe)], env=child_env, check=False)
    isolation_guard.clear_violations()


def test_guard_allows_isolated_python_child_bootstrap(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import os\n"
        "assert os.environ['LUMENX_TEST_ISOLATED'] == '1'\n"
        "print('isolated-child-ok')\n",
        encoding="utf-8",
    )
    child_env = os.environ.copy()
    child_env["LUMENX_TEST_ISOLATED"] = "1"
    helper = Path(os.environ["LUMENX_REPO_ROOT"]) / "scripts" / "test_support" / "isolated_child.py"
    result = subprocess.run(
        [sys.executable, "-S", str(helper), str(probe)],
        env=child_env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "isolated-child-ok"


def test_guard_blocks_subprocess_during_collection_phase(monkeypatch):
    monkeypatch.setenv("LUMENX_TEST_PHASE", "collecting")
    with pytest.raises(RuntimeError, match="collection phase"):
        subprocess.run(["ffmpeg", "-version"], check=False)
    isolation_guard.clear_violations()


def test_guard_blocks_fake_ffmpeg_even_with_matching_basename(tmp_path, monkeypatch):
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    child_env = os.environ.copy()
    child_env["PATH"] = str(tmp_path)
    with pytest.raises(RuntimeError, match="untrusted ffmpeg"):
        subprocess.run(["ffmpeg", "-version"], env=child_env, check=False)
    isolation_guard.clear_violations()


def test_guard_blocks_ffmpeg_network_urls():
    ffmpeg = os.environ.get("LUMENX_TEST_FFMPEG") or "ffmpeg"
    with pytest.raises(RuntimeError, match="network URL"):
        subprocess.run([ffmpeg, "-i", "https://example.invalid/video.mp4", "out.mp4"], check=False)
    isolation_guard.clear_violations()


def test_guard_blocks_rename_from_owned_outside_sentinel(tmp_path):
    source = _sentinel_file()
    target = tmp_path / "sentinel-moved.txt"
    with pytest.raises(RuntimeError, match="rename source.*outside isolated root"):
        os.rename(source, target)
    assert source.exists()
    assert not target.exists()
    isolation_guard.clear_violations()


def test_bytes_paths_do_not_bypass_guard():
    with pytest.raises(RuntimeError, match="blocked read"):
        with open(os.fsencode(_sentinel_file()), "rb"):
            pass
    isolation_guard.clear_violations()


def test_python_cannot_place_code_before_bootstrap():
    helper = Path(os.environ["LUMENX_REPO_ROOT"]) / "scripts/test_support/isolated_child.py"
    with pytest.raises(RuntimeError, match="Python subprocess"):
        subprocess.run([sys.executable, "-S", "-c", "print('no side effects')", str(helper)], check=False)
    isolation_guard.clear_violations()


def test_dir_fd_write_is_checked_against_real_directory():
    folder = os.open(os.environ["LUMENX_REPO_ROOT"], os.O_RDONLY)
    try:
        with pytest.raises(RuntimeError, match="blocked"):
            descriptor = os.open("README.md", os.O_WRONLY, dir_fd=folder)
            os.close(descriptor)
    finally:
        os.close(folder)
    isolation_guard.clear_violations()


def test_ffmpeg_cannot_accept_source_repository_as_output():
    ffmpeg = os.environ.get("LUMENX_TEST_FFMPEG") or "ffmpeg"
    output = str(Path(os.environ["LUMENX_REPO_ROOT"]) / "README.md")
    with pytest.raises(RuntimeError, match="outside isolated root"):
        subprocess.run([ffmpeg, "-version", output], capture_output=True, check=False)
    isolation_guard.clear_violations()


def test_caught_child_violation_still_fails_process(tmp_path):
    probe = tmp_path / 'caught.py'
    probe.write_text(
        "import os\n"
        "try:\n"
        "    open(os.environ['LUMENX_TEST_SENTINEL_FILE']).read()\n"
        "except RuntimeError:\n"
        "    pass\n"
    )
    helper = Path(os.environ['LUMENX_REPO_ROOT']) / 'scripts/test_support/isolated_child.py'
    result = subprocess.run([sys.executable, '-S', str(helper), str(probe)], capture_output=True, text=True)
    assert result.returncode == 97
    assert 'LUMENX_TEST_ISOLATION_VIOLATION' in result.stderr


def test_collection_is_guarded_even_outside_repository_conftest(tmp_path):
    probe = tmp_path / 'test_collection_probe.py'
    probe.write_text(
        "import subprocess\n"
        "try:\n"
        "    subprocess.run(['ffmpeg', '-version'])\n"
        "except RuntimeError:\n"
        "    pass\n"
        "def test_noop(): pass\n"
    )
    helper = Path(os.environ['LUMENX_REPO_ROOT']) / 'scripts/test_support/isolated_pytest.py'
    result = subprocess.run([sys.executable, '-S', str(helper), '-p', 'no:cacheprovider', '--collect-only', str(probe)], capture_output=True, text=True)
    assert result.returncode == 97
    assert 'collection phase' in result.stderr


def test_symlink_cannot_point_outside_isolation(tmp_path):
    with pytest.raises(RuntimeError, match='symlink target'):
        (tmp_path / 'escape').symlink_to(_sentinel_file())
    isolation_guard.clear_violations()


def test_existing_repository_runtime_data_is_unreadable():
    path = Path(os.environ['LUMENX_REPO_ROOT']) / 'output/projects.json'
    with pytest.raises(RuntimeError, match='repository runtime data'):
        path.read_text()
    isolation_guard.clear_violations()


def test_executable_override_cannot_bypass_allowlist(tmp_path):
    probe = tmp_path / 'noop.py'
    probe.write_text('pass\n')
    helper = Path(os.environ['LUMENX_REPO_ROOT']) / 'scripts/test_support/isolated_child.py'
    with pytest.raises(RuntimeError, match='executable override'):
        subprocess.run([sys.executable, '-S', str(helper), str(probe)], executable='/bin/echo')
    isolation_guard.clear_violations()


def test_real_ffmpeg_allows_synthetic_lavfi_and_valid_concat_manifest(tmp_path):
    ffmpeg = os.environ.get('LUMENX_TEST_FFMPEG')
    if not ffmpeg:
        pytest.skip('FFmpeg is not installed')
    one = tmp_path / 'one.mp4'
    two = tmp_path / 'two.mp4'
    for target, color in ((one, 'red'), (two, 'blue')):
        result = subprocess.run([ffmpeg, '-y', '-f', 'lavfi', '-i', f'color=c={color}:s=64x64:d=0.1', '-pix_fmt', 'yuv420p', str(target)], capture_output=True)
        assert result.returncode == 0, result.stderr.decode()
        assert target.stat().st_size > 0
    playlist = tmp_path / 'inputs.txt'
    playlist.write_text(f"file '{one}'\nfile '{two}'\n")
    result = subprocess.run([ffmpeg, '-f', 'concat', '-safe', '0', '-i', str(playlist), str(tmp_path / 'merged.mp4')], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def test_ffmpeg_rejects_malformed_or_external_concat_manifest(tmp_path):
    ffmpeg = os.environ.get('LUMENX_TEST_FFMPEG') or 'ffmpeg'
    playlist = tmp_path / 'inputs.txt'
    playlist.write_text("file 'https://example.invalid/video.mp4'\n")
    with pytest.raises(RuntimeError, match='concat manifest'):
        subprocess.run([ffmpeg, '-f', 'concat', '-safe', '0', '-i', str(playlist), str(tmp_path / 'merged.mp4')])
    isolation_guard.clear_violations()


def test_ffmpeg_concat_manifest_rejects_symlink_escape(tmp_path):
    ffmpeg = os.environ.get('LUMENX_TEST_FFMPEG') or 'ffmpeg'
    outside = _sentinel_file()
    escaped = tmp_path / 'escaped.mp4'
    with pytest.raises(RuntimeError, match='symlink target'):
        escaped.symlink_to(outside)
    isolation_guard.clear_violations()
    playlist = tmp_path / 'inputs.txt'
    playlist.write_text(f"file '{outside}'\n")
    with pytest.raises(RuntimeError, match='concat manifest'):
        subprocess.run([ffmpeg, '-f', 'concat', '-safe', '0', '-i', str(playlist), str(tmp_path / 'merged.mp4')])
    isolation_guard.clear_violations()


def test_ffmpeg_multiple_inputs_require_lavfi_or_isolated_files(tmp_path):
    ffmpeg = os.environ.get('LUMENX_TEST_FFMPEG') or 'ffmpeg'
    with pytest.raises(RuntimeError, match='unsupported ffmpeg input'):
        subprocess.run([ffmpeg, '-i', str(tmp_path / 'one.mp4'), '-i', str(tmp_path / 'list.m3u8')])
    isolation_guard.clear_violations()


def test_ffmpeg_pipe_protocol_remains_blocked(tmp_path):
    ffmpeg = os.environ.get('LUMENX_TEST_FFMPEG') or 'ffmpeg'
    with pytest.raises(RuntimeError, match='indirect media protocol'):
        subprocess.run([ffmpeg, '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.1', '-f', 's16le', 'pipe:1'])
    isolation_guard.clear_violations()




def test_ffmpeg_plain_input_does_not_receive_concat_whitelist(tmp_path, monkeypatch):
    ffmpeg = os.environ.get('LUMENX_TEST_FFMPEG') or 'ffmpeg'
    disguised = tmp_path / 'disguised.mp4'
    disguised.write_text("ffconcat version 1.0\nfile 'inside.mp4'\n", encoding='utf-8')
    captured = _capture_real_popen(monkeypatch)

    subprocess.run([ffmpeg, '-i', str(disguised), str(tmp_path / 'out.mp4')], capture_output=True)

    index = captured['args'].index('-i')
    assert captured['args'][index - 1] == _MEDIA_FORMATS
    assert 'concat' not in captured['args'][index - 1].split(',')


def test_ffprobe_plain_input_does_not_receive_concat_whitelist(tmp_path, monkeypatch):
    ffprobe = os.environ.get('LUMENX_TEST_FFPROBE') or 'ffprobe'
    disguised = tmp_path / 'disguised.mp4'
    disguised.write_text("ffconcat version 1.0\nfile 'inside.mp4'\n", encoding='utf-8')
    captured = _capture_real_popen(monkeypatch)

    subprocess.run([ffprobe, str(disguised)], capture_output=True)

    assert captured['args'][1:5] == _limits()


def test_ffmpeg_explicit_concat_is_the_only_concat_whitelisted_input(tmp_path, monkeypatch):
    ffmpeg = os.environ.get('LUMENX_TEST_FFMPEG') or 'ffmpeg'
    first = tmp_path / 'one.mp4'
    second = tmp_path / 'two.mp4'
    first.write_bytes(b'not-real-media')
    second.write_bytes(b'not-real-media')
    playlist = tmp_path / 'inputs.txt'
    playlist.write_text(f"file '{first}'\nfile '{second}'\n")
    captured = _capture_real_popen(monkeypatch)

    subprocess.run([ffmpeg, '-f', 'concat', '-safe', '0', '-i', str(playlist), '-i', str(first), str(tmp_path / 'out.mp4')], capture_output=True)

    input_indexes = [index for index, value in enumerate(captured['args']) if value == '-i']
    first_whitelist = captured['args'][input_indexes[0] - 1]
    second_whitelist = captured['args'][input_indexes[1] - 1]
    assert 'concat' in first_whitelist.split(',')
    assert 'concat' not in second_whitelist.split(',')


def test_ffmpeg_concat_manifest_rejects_nested_concat_target(tmp_path):
    ffmpeg = os.environ.get('LUMENX_TEST_FFMPEG') or 'ffmpeg'
    nested = tmp_path / 'nested.mp4'
    nested.write_text("ffconcat version 1.0\nfile 'inside.mp4'\n", encoding='utf-8')
    playlist = tmp_path / 'inputs.txt'
    playlist.write_text(f"file '{nested}'\n")
    with pytest.raises(RuntimeError, match='nested concat'):
        subprocess.run([ffmpeg, '-f', 'concat', '-safe', '0', '-i', str(playlist), str(tmp_path / 'merged.mp4')])
    isolation_guard.clear_violations()


def test_ffprobe_cannot_enable_concat_through_explicit_input(tmp_path, monkeypatch):
    ffprobe = os.environ.get('LUMENX_TEST_FFPROBE') or 'ffprobe'
    disguised = tmp_path / 'disguised.mp4'
    disguised.write_text("ffconcat version 1.0\nfile 'inside.mp4'\n", encoding='utf-8')
    captured = _capture_real_popen(monkeypatch)
    with pytest.raises(RuntimeError, match='ffprobe concat'):
        subprocess.run([ffprobe, '-f', 'concat', '-i', str(disguised)], capture_output=True)
    assert not captured
    isolation_guard.clear_violations()


def test_ffmpeg_concat_manifest_rejects_whitespace_only_manifest(tmp_path):
    ffmpeg = os.environ.get('LUMENX_TEST_FFMPEG') or 'ffmpeg'
    playlist = tmp_path / 'inputs.txt'
    playlist.write_text('   \n\t\n')
    with pytest.raises(RuntimeError, match='empty'):
        subprocess.run([ffmpeg, '-f', 'concat', '-safe', '0', '-i', str(playlist), str(tmp_path / 'merged.mp4')])
    isolation_guard.clear_violations()


@pytest.mark.parametrize('unsafe_value', [
    "clip'name.mp4",
    r"clip\name.mp4",
    "clip\0name.mp4",
    "clip\x1fname.mp4",
])
def test_ffmpeg_concat_manifest_rejects_ambiguous_path_tokens(tmp_path, unsafe_value):
    ffmpeg = os.environ.get('LUMENX_TEST_FFMPEG') or 'ffmpeg'
    safe = tmp_path / 'safe.mp4'
    safe.write_bytes(b'not-real-media')
    playlist = tmp_path / 'inputs.txt'
    playlist.write_text(f"file '{unsafe_value}'\n", encoding='utf-8')
    with pytest.raises(RuntimeError, match='unsupported path token'):
        subprocess.run([ffmpeg, '-f', 'concat', '-safe', '0', '-i', str(playlist), str(tmp_path / 'merged.mp4')])
    isolation_guard.clear_violations()


def test_ffmpeg_without_explicit_limits_gets_safe_limits_injected(tmp_path, monkeypatch):
    ffmpeg = os.environ.get('LUMENX_TEST_FFMPEG') or 'ffmpeg'
    source = tmp_path / 'one.mp4'
    source.write_bytes(b'not-real-media')
    captured = _capture_real_popen(monkeypatch)

    subprocess.run([ffmpeg, '-i', str(source), str(tmp_path / 'out.mp4')], capture_output=True)

    index = captured['args'].index('-i')
    assert captured['args'][index - 4:index] == _limits()


def test_ffmpeg_rejects_expanded_or_ambiguous_whitelists(tmp_path):
    ffmpeg = os.environ.get('LUMENX_TEST_FFMPEG') or 'ffmpeg'
    source = tmp_path / 'one.mp4'
    source.write_bytes(b'not-real-media')
    with pytest.raises(RuntimeError, match='protocol whitelist exceeds'):
        subprocess.run([ffmpeg, '-protocol_whitelist', 'file,http', '-format_whitelist', _MEDIA_FORMATS, '-i', str(source)], capture_output=True)
    isolation_guard.clear_violations()
    with pytest.raises(RuntimeError, match='format_whitelist has duplicate'):
        subprocess.run([ffmpeg, '-protocol_whitelist', 'file', '-format_whitelist', f'{_MEDIA_FORMATS},mov', '-i', str(source)], capture_output=True)
    isolation_guard.clear_violations()
    with pytest.raises(RuntimeError, match='explicit restricted'):
        subprocess.run([ffmpeg, '-protocol_whitelist', 'file', '-i', str(source)], capture_output=True)
    isolation_guard.clear_violations()


def test_ffmpeg_allows_bmp_file_only_raster_demuxer(tmp_path, monkeypatch):
    ffmpeg = os.environ.get('LUMENX_TEST_FFMPEG') or 'ffmpeg'
    source = tmp_path / 'one.bmp'
    source.write_bytes(b'BMnot-real-media')
    captured = _capture_real_popen(monkeypatch)

    subprocess.run([ffmpeg, '-f', 'image2', *_input(source)], capture_output=True)

    index = captured['args'].index('-i')
    assert captured['args'][index - 4:index] == _limits()
    assert captured['args'][index + 1] == str(source)


def test_ffmpeg_bmp_allowlist_still_blocks_protocol_and_path_escape(tmp_path):
    ffmpeg = os.environ.get('LUMENX_TEST_FFMPEG') or 'ffmpeg'
    source = tmp_path / 'one.bmp'
    source.write_bytes(b'BMnot-real-media')

    with pytest.raises(RuntimeError, match='protocol whitelist exceeds'):
        subprocess.run(
            [ffmpeg, '-protocol_whitelist', 'file,http', '-format_whitelist', _MEDIA_FORMATS, '-i', str(source)],
            capture_output=True,
        )
    isolation_guard.clear_violations()
    with pytest.raises(RuntimeError, match='outside isolated root'):
        subprocess.run([ffmpeg, *_input(_sentinel_file().parent / 'escape.bmp')], capture_output=True)
    isolation_guard.clear_violations()


def test_child_cwd_cannot_change_relative_path_meaning(tmp_path):
    ffmpeg = os.environ.get('LUMENX_TEST_FFMPEG') or 'ffmpeg'
    with pytest.raises(RuntimeError, match='child cwd'):
        subprocess.run([ffmpeg, '-version'], cwd=tmp_path)
    isolation_guard.clear_violations()


@pytest.mark.parametrize("token", ["zmq", "sendcmd", "drawtext", "movie="])
@pytest.mark.parametrize("tool", ["ffprobe", "ffmpeg"])
def test_media_guard_does_not_treat_owned_absolute_paths_as_filter_graphs(tmp_path, monkeypatch, token, tool):
    source = tmp_path / f"frozen-{token}-clip.wav"
    source.write_bytes(b"media-probe-fixture")
    captured = _capture_real_popen(monkeypatch)
    executable = os.environ[f"LUMENX_TEST_{tool.upper()}"]
    args = (["-v", "error", "-of", "json", str(source)] if tool == "ffprobe" else
            ["-i", str(source), str(tmp_path / f"rendered-{token}.mp4")])
    try:
        subprocess.run([executable, *args], capture_output=True)
        assert str(source) in captured["args"]
    finally:
        isolation_guard.clear_violations()


@pytest.mark.parametrize("graph", ["movie=/tmp/owned.mp4", "amovie=/tmp/owned.wav", "zmq", "drawtext=text=hello"])
def test_media_guard_still_blocks_indirect_filter_graph_values(tmp_path, graph):
    ffmpeg = os.environ["LUMENX_TEST_FFMPEG"]
    with pytest.raises(RuntimeError, match="indirect filter"):
        subprocess.run([ffmpeg, "-filter_complex", graph, str(tmp_path / "out.mp4")], capture_output=True)
    isolation_guard.clear_violations()


def test_filter_named_absolute_path_still_cannot_escape_isolation(tmp_path):
    outside = _sentinel_file().parent / "zmq.wav"
    ffprobe = os.environ["LUMENX_TEST_FFPROBE"]
    with pytest.raises(RuntimeError, match="outside isolated root"):
        subprocess.run([ffprobe, str(outside)], capture_output=True)
    isolation_guard.clear_violations()
