import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import Script
from ...utils import get_logger
from ...utils.media_refs import output_media_ref, resolve_local_media_path
from ...utils.system_check import get_ffmpeg_install_instructions, get_ffmpeg_path

logger = get_logger(__name__)

RESOLUTION_PRESETS: Dict[str, Tuple[int, int]] = {
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "4k": (3840, 2160),
}

OUTPUT_FORMATS = {"mp4", "mov", "gif"}

TRACK_VOLUME = {
    "source": 0.55,
    "dialogue": 1.0,
    "sfx": 0.82,
    "bgm": 0.34,
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _ffprobe_path() -> Optional[str]:
    ffmpeg_path = get_ffmpeg_path()
    if ffmpeg_path:
        candidate = Path(ffmpeg_path).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if candidate.exists():
            return str(candidate)
    return shutil.which("ffprobe")


class ExportManager:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.project_root = _project_root()
        self.output_dir = self._resolve_workspace_path(self.config.get("output_dir", "output/export"))
        os.makedirs(self.output_dir, exist_ok=True)

    def _resolve_workspace_path(self, value: str | Path) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path.resolve()
        return (self.project_root / path).resolve()

    def _relative_path(self, path: Path) -> str:
        return Path(os.path.relpath(str(path), str(self.project_root))).as_posix()

    def _resolve_media_path(self, value: Optional[str]) -> Optional[Path]:
        if not value:
            return None
        resolved = resolve_local_media_path(value, project_root=str(self.project_root))
        if resolved:
            candidate = Path(resolved).resolve()
            return candidate if candidate.exists() else None

        candidate = Path(value)
        if candidate.exists():
            return candidate.resolve()
        return None

    def _resolve_output_format(self, value: str) -> str:
        normalized = (value or "mp4").strip().lower()
        return normalized if normalized in OUTPUT_FORMATS else "mp4"

    def _resolve_resolution(self, value: str) -> Tuple[int, int]:
        normalized = (value or "1080p").strip().lower()
        return RESOLUTION_PRESETS.get(normalized, RESOLUTION_PRESETS["1080p"])

    def _run_ffmpeg(self, args: List[str], *, cwd: Optional[Path] = None, timeout: int = 600) -> None:
        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path:
            raise RuntimeError(
                "FFmpeg is required for export but was not found.\n\n"
                f"{get_ffmpeg_install_instructions()}"
            )

        cmd = [ffmpeg_path, "-y", *args]
        logger.debug("Running FFmpeg: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(cwd or self.project_root),
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.error("FFmpeg failed: %s", result.stderr.strip())
            raise RuntimeError(result.stderr.strip() or "FFmpeg export failed")

    def _probe_duration(self, path: Path) -> Optional[float]:
        probe = _ffprobe_path()
        if not probe:
            return None

        try:
            result = subprocess.run(
                [
                    probe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                cwd=str(self.project_root),
                timeout=20,
            )
            if result.returncode != 0:
                return None
            return float(result.stdout.strip())
        except Exception:
            return None

    def _has_audio_stream(self, path: Path) -> bool:
        probe = _ffprobe_path()
        if not probe:
            return False

        try:
            result = subprocess.run(
                [
                    probe,
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=index",
                    "-of",
                    "csv=p=0",
                    str(path),
                ],
                capture_output=True,
                text=True,
                cwd=str(self.project_root),
                timeout=20,
            )
            return bool(result.stdout.strip())
        except Exception:
            return False

    def _frame_duration(self, script: Script, frame: Any) -> float:
        if frame.selected_video_id:
            task = next((item for item in script.video_tasks if item.id == frame.selected_video_id), None)
        else:
            task = next((item for item in script.video_tasks if item.video_url == frame.video_url), None)

        if task and task.duration:
            try:
                return max(1.0, float(task.duration))
            except (TypeError, ValueError):
                pass

        return 5.0

    def _timecode(self, seconds: float) -> str:
        total = max(0, int(round(seconds * 1000)))
        hours = total // 3_600_000
        minutes = (total % 3_600_000) // 60_000
        secs = (total % 60_000) // 1000
        millis = total % 1000
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _build_srt(self, script: Script, subtitle_path: Path) -> None:
        cursor = 0.0
        entries: List[str] = []

        for frame in script.frames:
            duration = self._frame_duration(script, frame)
            if frame.dialogue:
                start = cursor
                end = cursor + duration
                text = str(frame.dialogue).strip()
                if text:
                    entries.append(
                        "\n".join(
                            [
                                str(len(entries) + 1),
                                f"{self._timecode(start)} --> {self._timecode(end)}",
                                text,
                                "",
                            ]
                        )
                    )
            cursor += duration

        subtitle_path.parent.mkdir(parents=True, exist_ok=True)
        subtitle_path.write_text("\n".join(entries), encoding="utf-8")

    def _build_filter_chain(self, resolution: str, subtitle_path: Optional[Path]) -> str:
        width, height = self._resolve_resolution(resolution)
        filters = [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        ]
        if subtitle_path:
            filters.append(f"subtitles={self._relative_path(subtitle_path)}")
        return ",".join(filters)

    def _extract_source_audio(self, input_video: Path, work_dir: Path) -> Optional[Path]:
        if not self._has_audio_stream(input_video):
            return None

        extracted = work_dir / "source_audio.wav"
        self._run_ffmpeg(
            [
                "-i",
                self._relative_path(input_video),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "44100",
                "-c:a",
                "pcm_s16le",
                self._relative_path(extracted),
            ],
            cwd=self.project_root,
            timeout=180,
        )
        return extracted if extracted.exists() else None

    def _concat_audio_tracks(self, audio_paths: List[Path], output_path: Path) -> Optional[Path]:
        usable = [path for path in audio_paths if path and path.exists()]
        if not usable:
            return None

        if len(usable) == 1:
            if usable[0] == output_path:
                return output_path
            shutil.copyfile(usable[0], output_path)
            return output_path

        concat_list = output_path.with_suffix(".concat.txt")
        concat_list.write_text(
            "\n".join(f"file '{path.as_posix()}'" for path in usable),
            encoding="utf-8",
        )

        self._run_ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                self._relative_path(concat_list),
                "-c:a",
                "pcm_s16le",
                self._relative_path(output_path),
            ],
            cwd=self.project_root,
            timeout=240,
        )
        return output_path if output_path.exists() else None

    def _mix_audio_layers(self, layers: List[Tuple[Path, float, float]], output_path: Path) -> Optional[Path]:
        usable = [(path, delay, volume) for path, delay, volume in layers if path and path.exists()]
        if not usable:
            return None

        if len(usable) == 1 and usable[0][1] == 0:
            if usable[0][0] == output_path:
                return output_path
            shutil.copyfile(usable[0][0], output_path)
            return output_path

        args: List[str] = []
        for path, _, _ in usable:
            args.extend(["-i", self._relative_path(path)])

        filter_parts: List[str] = []
        mapped_names: List[str] = []
        for index, (_, delay, volume) in enumerate(usable):
            name = f"a{index}"
            mapped_names.append(f"[{name}]")
            filter_parts.append(
                f"[{index}:a]aformat=sample_rates=44100:channel_layouts=mono,"
                f"volume={volume:.3f},adelay={int(delay)}[{name}]"
            )

        filter_parts.append(
            "".join(mapped_names)
            + f"amix=inputs={len(mapped_names)}:duration=longest:dropout_transition=2[mix]"
        )
        args.extend(
            [
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                "[mix]",
                "-ac",
                "1",
                "-ar",
                "44100",
                "-c:a",
                "pcm_s16le",
                self._relative_path(output_path),
            ]
        )

        self._run_ffmpeg(args, cwd=self.project_root, timeout=300)
        return output_path if output_path.exists() else None

    def _build_audio_mix(self, script: Script, input_video: Path, work_dir: Path) -> Optional[Path]:
        stem_paths: List[Path] = []

        source_audio = self._extract_source_audio(input_video, work_dir)
        if source_audio:
            stem_paths.append(source_audio)

        dialogue_layers: List[Tuple[Path, float, float]] = []
        sfx_layers: List[Tuple[Path, float, float]] = []
        bgm_tracks: List[Path] = []
        cursor = 0.0

        for frame in script.frames:
            duration = self._frame_duration(script, frame)
            if frame.audio_url:
                dialogue_path = self._resolve_media_path(frame.audio_url)
                if dialogue_path:
                    dialogue_layers.append((dialogue_path, cursor * 1000.0, TRACK_VOLUME["dialogue"]))
            if frame.sfx_url:
                sfx_path = self._resolve_media_path(frame.sfx_url)
                if sfx_path:
                    sfx_layers.append((sfx_path, cursor * 1000.0, TRACK_VOLUME["sfx"]))
            if frame.bgm_url:
                bgm_path = self._resolve_media_path(frame.bgm_url)
                if bgm_path:
                    bgm_tracks.append(bgm_path)
            cursor += duration

        dialogue_mix = self._mix_audio_layers(dialogue_layers, work_dir / "dialogue_mix.wav")
        if dialogue_mix:
            stem_paths.append(dialogue_mix)

        sfx_mix = self._mix_audio_layers(sfx_layers, work_dir / "sfx_mix.wav")
        if sfx_mix:
            stem_paths.append(sfx_mix)

        bgm_mix = self._concat_audio_tracks(bgm_tracks, work_dir / "bgm_mix.wav")
        if bgm_mix:
            stem_paths.append(bgm_mix)

        if not stem_paths:
            return None

        if len(stem_paths) == 1:
            return stem_paths[0]

        final_mix = work_dir / "final_mix.wav"
        layers = []
        for path in stem_paths:
            layer_name = path.stem
            volume = TRACK_VOLUME["source"] if layer_name == "source_audio" else TRACK_VOLUME.get(
                "dialogue" if "dialogue" in layer_name else "sfx" if "sfx" in layer_name else "bgm",
                0.5,
            )
            layers.append((path, 0.0, volume))
        return self._mix_audio_layers(layers, final_mix)

    def _render_gif(self, input_video: Path, output_path: Path, resolution: str, subtitle_path: Optional[Path]) -> None:
        width, height = self._resolve_resolution(resolution)
        filters = [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        ]
        if subtitle_path:
            filters.append(f"subtitles={self._relative_path(subtitle_path)}")
        filters.append("fps=12")
        filter_expr = ",".join(filters)

        self._run_ffmpeg(
            [
                "-i",
                self._relative_path(input_video),
                "-filter_complex",
                f"[0:v]{filter_expr},split[v0][v1];[v0]palettegen[p];[v1][p]paletteuse",
                self._relative_path(output_path),
            ],
            cwd=self.project_root,
            timeout=600,
        )

    def _render_video(
        self,
        input_video: Path,
        output_path: Path,
        resolution: str,
        subtitle_path: Optional[Path],
        audio_mix: Optional[Path],
    ) -> None:
        filter_chain = self._build_filter_chain(resolution, subtitle_path)
        args: List[str] = [
            "-i",
            self._relative_path(input_video),
        ]

        if audio_mix and audio_mix.exists():
            args.extend(["-i", self._relative_path(audio_mix)])
            map_args = ["-map", "0:v:0", "-map", "1:a:0"]
            audio_codec = ["-c:a", "aac"]
        else:
            map_args = ["-map", "0:v:0", "-map", "0:a?"]
            audio_codec = ["-c:a", "aac"]

        args.extend(
            [
                "-vf",
                filter_chain,
                *map_args,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                *audio_codec,
                "-movflags",
                "+faststart",
                self._relative_path(output_path),
            ]
        )

        self._run_ffmpeg(args, cwd=self.project_root, timeout=900)

    def render_project(self, script: Script, options: Dict[str, Any]) -> Dict[str, Optional[str]]:
        """
        Render the final export for the project.

        Returns a dict containing the video URL and, when requested, a subtitle URL.
        """
        logger.info("Starting export for project %s with options: %s", script.id, options)

        resolution = self._resolve_resolution(options.get("resolution", "1080p"))
        output_format = self._resolve_output_format(options.get("format", "mp4"))
        subtitles = (options.get("subtitles", "none") or "none").strip().lower()

        input_video = self._resolve_media_path(script.merged_video_url)
        if not input_video:
            raise ValueError("Merged video not found. Please merge the selected clips before export.")

        timestamp = int(time.time())
        export_base = f"{script.id}_{timestamp}"
        work_dir = self._resolve_workspace_path(Path(self.output_dir) / script.id / export_base)
        os.makedirs(work_dir, exist_ok=True)

        subtitle_path = None
        subtitle_url = None
        if subtitles in {"burn-in", "srt"}:
            subtitle_path = work_dir / f"{export_base}.srt"
            self._build_srt(script, subtitle_path)
            if subtitles == "srt":
                subtitle_url = output_media_ref(str(subtitle_path), project_root=str(self.project_root))

        audio_mix = None
        if output_format != "gif":
            audio_mix = self._build_audio_mix(script, input_video, work_dir)

        output_path = self.output_dir / f"{export_base}.{output_format}"

        if output_format == "gif":
            self._render_gif(input_video, output_path, options.get("resolution", "1080p"), subtitle_path if subtitles == "burn-in" else None)
        else:
            self._render_video(input_video, output_path, options.get("resolution", "1080p"), subtitle_path if subtitles == "burn-in" else None, audio_mix)

        logger.info("Export completed: %s", output_path)
        return {
            "url": output_media_ref(str(output_path), project_root=str(self.project_root)),
            "subtitle_url": subtitle_url,
            "subtitle_format": "srt" if subtitle_url else None,
            "format": output_format,
            "resolution": options.get("resolution", "1080p"),
        }
