"""Static media serving guard for LumenX managed output files."""

from __future__ import annotations

import os
from collections.abc import Iterable
from urllib.parse import unquote

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


MEDIA_SUFFIXES = frozenset({
    # Images accepted by the upload path and generated asset/storyboard flows.
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".ico",
    # Videos produced by task, merge, export, and playground flows.
    ".mp4", ".mov", ".webm", ".avi", ".m4v", ".mkv",
    # Audio produced by dialogue/dub/preview flows.
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac",
})


class ManagedMediaStaticFiles(StaticFiles):
    """Serve only managed media files while preserving Starlette file handling.

    Starlette still owns method handling, range responses, MIME headers, and
    realpath containment. This subclass validates requested and resolved media paths so that
    output-root JSON/config/lock files are not exposed by
    the catch-all /files mounts.
    """

    def __init__(self, *args, allowed_prefixes: Iterable[str] | None = None, **kwargs):
        super().__init__(*args, follow_symlink=False, **kwargs)
        self._allowed_prefixes = tuple(
            prefix.strip("/") for prefix in (allowed_prefixes or ()) if prefix.strip("/")
        )

    async def get_response(self, path: str, scope):
        if not self._is_allowed_media_path(path):
            raise HTTPException(status_code=404)
        response = await super().get_response(path, scope)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if response.headers.get("content-type", "").split(";", 1)[0] == "image/svg+xml":
            # Uploaded SVG must remain inert even when opened as a document.
            response.headers["Content-Security-Policy"] = "sandbox"
        return response

    def lookup_path(self, path: str):
        full_path, stat_result = super().lookup_path(path)
        if stat_result is None:
            return full_path, stat_result
        if not self._is_allowed_resolved_path(full_path):
            return "", None
        return full_path, stat_result

    def _is_allowed_resolved_path(self, full_path: str) -> bool:
        resolved = os.path.realpath(full_path)
        for directory in self.all_directories:
            root = os.path.realpath(directory)
            try:
                if os.path.commonpath([resolved, root]) != root:
                    continue
                relative = os.path.relpath(resolved, root)
            except ValueError:
                continue
            if self._is_allowed_media_path(relative):
                return True
        return False

    def _is_allowed_media_path(self, path: str) -> bool:
        decoded = unquote(path or "").replace("\\", "/")
        if not decoded or "\x00" in decoded:
            return False

        parts = decoded.split("/")
        if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
            return False

        lowered = decoded.lower()
        if not any(lowered.endswith(suffix) for suffix in MEDIA_SUFFIXES):
            return False

        if self._allowed_prefixes:
            first = parts[0]
            if first not in self._allowed_prefixes:
                return False

        return True
