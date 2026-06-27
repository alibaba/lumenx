"""TwelveLabs Pegasus scene analysis adapter (optional).

This is an *opt-in* video-understanding provider that augments the
Studio storyboard/synthesis pipeline with a natural-language scene
description of a *generated* video clip. It is fully inert unless the
user configures ``TWELVELABS_API_KEY`` — when no key is present every
public entry point returns ``None`` and the pipeline behaves exactly as
before.

Why this helps LumenX: the pipeline already produces an I2V clip per
storyboard frame (see ``src/apps/comic_gen/video.py``). Feeding that
clip back through Pegasus 1.5 yields a concise description of what the
model *actually* rendered (setting / mood / key action), which is useful
for QA ("did the shot match the prompt?"), auto-captioning, and review
notes — closing the loop on the generation step without changing any
default behavior.

The adapter follows the repo's existing model-adapter conventions
(cf. ``src/models/qwen_vl.py``):

* credentials read lazily from the environment (``TWELVELABS_API_KEY``);
* endpoint resolved via the shared ``{PROVIDER}_BASE_URL`` registry
  (``src/utils/endpoints.py``);
* no new third-party dependency — uses ``requests`` (already required).

Pegasus 1.5 needs a public URL or an uploaded asset; it does *not*
accept a bare video id, and the analyzed window must be >= 4s. A clip
URL must therefore be publicly reachable by the TwelveLabs API (e.g. an
OSS signed URL). When only a local path is available the analyzer skips
gracefully rather than failing the pipeline.

Get a free API key at https://twelvelabs.io (generous free tier).
"""

import os
import time
import logging
from typing import Optional

import requests

from ..utils.endpoints import get_provider_base_url

logger = logging.getLogger(__name__)

# Default prompt: a tight, reviewer-friendly scene summary.
DEFAULT_SCENE_PROMPT = (
    "Describe this video shot in 2-3 sentences: the setting, the main "
    "subjects, the mood, and the key action. Be concise and factual."
)

# Pegasus 1.5 requires the analyzed window to be at least 4 seconds.
MIN_ANALYZABLE_SECONDS = 4

# Async-task polling bounds. Pegasus on a short clip is usually ready in
# seconds; cap the wait so a stuck task can never block the pipeline.
_POLL_INTERVAL_SECONDS = 5
_POLL_TIMEOUT_SECONDS = 180


class PegasusSceneAnalyzer:
    """Opt-in TwelveLabs Pegasus 1.5 scene analyzer.

    Construct it cheaply anywhere; it performs no network or credential
    work until :meth:`analyze_clip` is called. Use :attr:`is_configured`
    to gate optional behavior so the pipeline stays unchanged when no key
    is set.
    """

    def __init__(self, config: Optional[dict] = None):
        config = config or {}
        params = config.get("params", {}) if isinstance(config, dict) else {}
        self.model_name = params.get("model_name", "pegasus1.5")
        self.max_tokens = int(params.get("max_tokens", 2048))
        self.timeout = float(params.get("timeout", _POLL_TIMEOUT_SECONDS))

    @property
    def api_key(self) -> Optional[str]:
        """Read the TwelveLabs key lazily from the environment.

        Returns ``None`` (rather than raising) when unset so callers can
        treat scene analysis as a no-op optional feature.
        """
        return os.getenv("TWELVELABS_API_KEY") or None

    @property
    def is_configured(self) -> bool:
        """True only when a TwelveLabs API key is available."""
        return bool(self.api_key)

    @property
    def base_url(self) -> str:
        """Resolve the API base URL via the shared endpoint registry.

        Honors ``TWELVELABS_BASE_URL`` like every other provider; falls
        back to the public v1.3 API host.
        """
        return get_provider_base_url("TWELVELABS")

    def analyze_clip(
        self,
        video_url: str,
        prompt: Optional[str] = None,
    ) -> Optional[str]:
        """Return a natural-language scene description for a clip URL.

        Args:
            video_url: A publicly reachable http(s) URL to the video clip
                (e.g. an OSS signed URL). Local file paths cannot be read
                by the TwelveLabs API and are skipped.
            prompt: Optional override of the default scene-summary prompt.

        Returns:
            The Pegasus-generated description, or ``None`` when the
            feature is disabled (no key), the input is not a usable public
            URL, or the request fails. Failures are logged and swallowed
            so optional analysis never breaks generation.
        """
        if not self.is_configured:
            logger.debug("TwelveLabs not configured; skipping scene analysis.")
            return None

        if not video_url or not video_url.startswith("http"):
            # Pegasus needs a public URL or an uploaded asset; a local
            # relative path (the pipeline's default) is not analyzable.
            logger.info(
                "Pegasus scene analysis skipped: clip is not a public URL "
                "(got %r). Configure OSS to mirror clips for analysis.",
                video_url,
            )
            return None

        try:
            task_id = self._start_task(video_url, prompt or DEFAULT_SCENE_PROMPT)
            return self._await_result(task_id)
        except requests.RequestException as exc:
            logger.warning("Pegasus scene analysis request failed: %s", exc)
            return None
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Pegasus scene analysis error: %s", exc)
            return None

    # -- internal helpers ---------------------------------------------------

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key, "Content-Type": "application/json"}

    def _start_task(self, video_url: str, prompt: str) -> str:
        """POST an async analyze task and return its id."""
        payload = {
            "model_name": self.model_name,
            "video_url": video_url,
            "prompt": prompt,
            "max_tokens": self.max_tokens,
        }
        logger.info("Submitting Pegasus analyze task for clip: %s", video_url)
        resp = requests.post(
            f"{self.base_url}/analyze",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        task_id = resp.json().get("_id") or resp.json().get("id")
        if not task_id:
            raise RuntimeError("TwelveLabs analyze response missing task id")
        return task_id

    def _await_result(self, task_id: str) -> Optional[str]:
        """Poll the analyze task until it is ready, failed, or times out."""
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            resp = requests.get(
                f"{self.base_url}/analyze/{task_id}",
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
            body = resp.json()
            status = body.get("status")
            if status == "ready":
                data = body.get("data") or (body.get("result") or {}).get("data")
                if isinstance(data, str) and data.strip():
                    logger.info("Pegasus scene analysis ready (%d chars).", len(data))
                    return data.strip()
                logger.warning("Pegasus task ready but returned no text.")
                return None
            if status == "failed":
                logger.warning(
                    "Pegasus analyze task failed: %s",
                    body.get("error") or "unknown error",
                )
                return None
            time.sleep(_POLL_INTERVAL_SECONDS)

        logger.warning("Pegasus analyze task %s timed out.", task_id)
        return None
