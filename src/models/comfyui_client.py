"""ComfyUI API client (dual-mode).

Supports two server protocols:

- ``standard`` (default): vanilla ComfyUI (``POST /prompt``, ``GET /history``,
  ``POST /upload/image``, ``GET /view``).
- ``zealman``: the ZEALMAN ComfyUI control panel REST API used by
  the lumenx-comfyui fork (``/api/workflow/generate``, ``/api/workflow/result``,
  ``/api/comfy/upload/file``, ``/api/comfy/view``, ``/api/workflow/list``).

Environment variables:

- ``COMFYUI_BASE_URL`` — server URL (default ``http://localhost:8188``)
- ``COMFYUI_PROTOCOL`` — ``standard`` (default) or ``zealman``
- ``COMFYUI_API_KEY`` — optional bearer token
- ``COMFYUI_VERIFY_SSL`` — set to ``1`` to verify TLS certificates
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "")
    return value.strip().lower() in {"1", "true", "yes", "on"} if value else default


def load_workflow_mapping() -> Dict[str, Any]:
    """Load ``config/workflow_mapping.json`` (repo-local, mirrors lumenx-comfyui)."""
    candidates = [
        _repo_root() / "config" / "workflow_mapping.json",
        Path(os.getenv("COMFYUI_WORKFLOW_MAPPING", "")),
    ]
    for path in candidates:
        if path and path.is_file():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError) as exc:
                logger.warning("Failed to load ComfyUI workflow mapping %s: %s", path, exc)
    return {}


class ComfyUIClient:
    """Client for ComfyUI / ZEALMAN panel REST APIs."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        protocol: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = 30,
    ) -> None:
        self.base_url = (base_url or os.getenv("COMFYUI_BASE_URL", "http://localhost:8188")).rstrip("/")
        self.protocol = (protocol or os.getenv("COMFYUI_PROTOCOL", "standard")).strip().lower()
        if self.protocol not in ("zealman", "standard"):
            logger.warning(
                "Unknown COMFYUI_PROTOCOL %r, falling back to 'standard'", self.protocol
            )
            self.protocol = "standard"
        self.api_key = api_key or os.getenv("COMFYUI_API_KEY", "") or None
        self.timeout = timeout
        self.session = requests.Session()
        self.session.verify = _env_flag("COMFYUI_VERIFY_SSL", False)
        if self.api_key:
            self.session.headers["Authorization"] = f"Bearer {self.api_key}"
        logger.info(
            "ComfyUI Client initialized: base_url=%s protocol=%s", self.base_url, self.protocol
        )

    # ------------------------------------------------------------------
    # Health / introspection
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        try:
            if self.protocol == "standard":
                response = self.session.get(
                    urljoin(self.base_url, "/system_stats"), timeout=5
                )
            else:
                response = self.session.get(
                    urljoin(self.base_url, "/api/health"), timeout=5
                )
            return response.status_code == 200
        except Exception as exc:  # noqa: BLE001 - connectivity probe must not raise
            logger.error("ComfyUI health check failed: %s", exc)
            return False

    def list_workflows(self) -> List[Dict[str, Any]]:
        """List available workflows (panel API) or local workflow templates."""
        if self.protocol == "standard":
            return self._list_local_workflow_templates()
        try:
            response = self.session.get(
                urljoin(self.base_url, "/api/workflow/list"), timeout=10
            )
            response.raise_for_status()
            data = response.json()
            if data.get("success"):
                return list(data.get("workflows", []))
            logger.error("Failed to list workflows: %s", data)
            return []
        except Exception as exc:  # noqa: BLE001
            logger.error("Error listing workflows: %s", exc)
            return []

    def get_workflow_info(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        for wf in self.list_workflows():
            if wf.get("id") == workflow_id:
                return wf
        return None

    def find_workflow_by_pattern(self, patterns: Iterable[str]) -> Optional[str]:
        """Return the first workflow id whose id/name contains any pattern."""
        workflows = self.list_workflows()
        for pattern in patterns:
            needle = str(pattern).lower()
            for wf in workflows:
                wf_id = str(wf.get("id", "")).lower()
                wf_name = str(wf.get("name", "")).lower()
                if needle in wf_id or needle in wf_name:
                    logger.info("Found matching ComfyUI workflow: %s", wf.get("id"))
                    return wf.get("id")
        logger.warning("No ComfyUI workflow found matching patterns: %s", list(patterns))
        return None

    # ------------------------------------------------------------------
    # Task submission / status
    # ------------------------------------------------------------------

    def submit_workflow_task(
        self,
        workflow_id: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        upload_files: Optional[Dict[str, str]] = None,
        workflow: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Submit a workflow task and return the prompt/task id.

        Args:
            workflow_id: Panel workflow id, or template filename for standard mode.
            parameters: ``input_values`` (panel) or node inputs (standard).
            upload_files: mapping ``{node_id: local_file_path}`` uploaded first.
            workflow: optional full workflow JSON for standard mode (bypasses template).
        """
        parameters = parameters or {}
        upload_results: Dict[str, Any] = {}
        if upload_files:
            for node_id, file_path in upload_files.items():
                uploaded = self.upload_file(file_path)
                if uploaded:
                    upload_results[node_id] = uploaded

        try:
            if self.protocol == "standard":
                if workflow is None:
                    workflow = self._load_workflow_template(workflow_id or "")
                if not workflow:
                    logger.error("No ComfyUI workflow template available for %r", workflow_id)
                    return None
                prompt = self._apply_input_values(dict(workflow), parameters)
                if upload_results:
                    prompt = self._inject_uploaded_files(prompt, upload_results)
                payload = {"prompt": prompt, "client_id": "lumenx-comfyui-client"}
                logger.info(
                    "Submitting workflow to vanilla ComfyUI (template=%s)", workflow_id
                )
                response = self.session.post(
                    urljoin(self.base_url, "/prompt"), json=payload, timeout=self.timeout
                )
                response.raise_for_status()
                data = response.json()
                prompt_id = data.get("prompt_id")
                if not prompt_id:
                    logger.error("No prompt_id in ComfyUI response: %s", data)
                return prompt_id

            # ZEALMAN panel protocol
            payload = {
                "workflow_id": workflow_id,
                "input_values": parameters,
                "client_id": "lumenx-comfyui-client",
            }
            if upload_results:
                payload["uploaded_files"] = upload_results
            logger.info("Submitting workflow %s to ZEALMAN panel...", workflow_id)
            response = self.session.post(
                urljoin(self.base_url, "/api/workflow/generate"),
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("success"):
                error = data.get("error")
                if isinstance(error, dict):
                    error = error.get("message") or str(error)
                logger.error("Workflow submission failed: %s", error)
                return None
            return data.get("prompt_id")
        except Exception as exc:  # noqa: BLE001
            logger.error("Error submitting workflow task: %s", exc)
            return None

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Query task status; returns ``None`` on transport errors."""
        try:
            if self.protocol == "standard":
                response = self.session.get(
                    urljoin(self.base_url, f"/history/{task_id}"), timeout=10
                )
                if response.status_code == 404:
                    return {"status": "running", "prompt_id": task_id, "progress": 0}
                response.raise_for_status()
                history = response.json()
                entry = (history or {}).get(task_id)
                if not entry:
                    return {"status": "running", "prompt_id": task_id, "progress": 0}
                status_info = entry.get("status", {})
                status_str = status_info.get("status_str", "")
                if status_str in ("success", "completed"):
                    return {
                        "status": "completed",
                        "prompt_id": task_id,
                        "results": self._build_standard_results(entry),
                    }
                if status_str in ("error", "failed"):
                    messages = status_info.get("messages", [])
                    error = messages[-1][1] if messages else {"message": "ComfyUI task failed"}
                    return {
                        "status": "failed",
                        "prompt_id": task_id,
                        "error": str(error),
                    }
                return {"status": "running", "prompt_id": task_id, "progress": 0}

            response = self.session.get(
                urljoin(self.base_url, "/api/workflow/result"),
                params={"prompt_id": task_id},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("success"):
                logger.error("Task status query failed: %s", data.get("error"))
                return None
            if data.get("pending"):
                return {"status": "running", "prompt_id": task_id, "progress": 0}
            results = data.get("results", [])
            if results:
                return {
                    "status": "completed",
                    "prompt_id": task_id,
                    "results": results,
                    "files": results,
                }
            return {
                "status": "failed",
                "prompt_id": task_id,
                "error": "No results returned",
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Error getting task status: %s", exc)
            return None

    def wait_for_task_completion(
        self,
        task_id: str,
        timeout: int = 900,
        poll_interval: int = 5,
    ) -> Optional[Dict[str, Any]]:
        """Poll until the task completes/fails, or ``timeout`` expires."""
        start = time.time()
        while time.time() - start < timeout:
            status = self.get_task_status(task_id)
            if status is None:
                time.sleep(poll_interval)
                continue
            state = status.get("status") or status.get("state")
            if state in ("completed", "success", "finished"):
                logger.info("ComfyUI task %s completed", task_id)
                return status
            if state in ("failed", "error"):
                logger.error("ComfyUI task %s failed: %s", task_id, status.get("error"))
                return status
            logger.info(
                "ComfyUI task %s in progress: %s%%",
                task_id,
                status.get("progress", 0),
            )
            time.sleep(poll_interval)
        logger.error("ComfyUI task %s timed out after %ss", task_id, timeout)
        return None

    # ------------------------------------------------------------------
    # File transfer
    # ------------------------------------------------------------------

    def upload_file(
        self, file_path: str, file_type: str = "input"
    ) -> Optional[Dict[str, Any]]:
        """Upload a local file; returns ``{"filename", "subfolder", "type"}``."""
        try:
            if not file_path or not os.path.exists(file_path):
                logger.error("File not found for ComfyUI upload: %s", file_path)
                return None
            file_name = os.path.basename(file_path)
            with open(file_path, "rb") as fh:
                files = {"file": (file_name, fh, "application/octet-stream")}
                data = {"type": file_type, "overwrite": "true"}
                if self.protocol == "standard":
                    response = self.session.post(
                        urljoin(self.base_url, "/upload/image"),
                        files={"image": (file_name, fh, "application/octet-stream")},
                        data=data,
                        timeout=60,
                    )
                else:
                    response = self.session.post(
                        urljoin(self.base_url, "/api/comfy/upload/file"),
                        files=files,
                        data=data,
                        timeout=60,
                    )
            response.raise_for_status()
            result = response.json()
            filename = (
                result.get("filename")
                or result.get("name")
                or file_name
            )
            logger.info("File uploaded to ComfyUI: %s", filename)
            return {
                "filename": filename,
                "subfolder": result.get("subfolder", ""),
                "type": result.get("type", file_type),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Error uploading file to ComfyUI: %s", exc)
            return None

    def download_file(
        self,
        filename: str,
        output_path: str,
        file_type: str = "output",
        subfolder: str = "",
    ) -> bool:
        """Download a generated file from ComfyUI to ``output_path``."""
        try:
            if self.protocol == "standard":
                params: Dict[str, Any] = {
                    "filename": filename,
                    "type": file_type,
                }
                if subfolder:
                    params["subfolder"] = subfolder
                response = self.session.get(
                    urljoin(self.base_url, "/view"),
                    params=params,
                    timeout=120,
                    stream=True,
                )
            else:
                response = self.session.get(
                    urljoin(self.base_url, "/api/comfy/view"),
                    params={"filename": filename, "type": file_type},
                    timeout=120,
                    stream=True,
                )
            response.raise_for_status()
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "wb") as fh:
                for chunk in response.iter_content(chunk_size=8192):
                    fh.write(chunk)
            logger.info("Downloaded ComfyUI file to %s", output_path)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Error downloading file from ComfyUI: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Standard-mode workflow templates
    # ------------------------------------------------------------------

    def _template_dirs(self) -> List[Path]:
        root = _repo_root()
        return [
            root / "config" / "comfyui_workflows",
            root / "config" / "workflow_templates",
        ]

    def _load_workflow_template(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        if not workflow_id:
            return None
        candidate = Path(workflow_id)
        if candidate.is_file():
            return self._read_template(candidate)
        for directory in self._template_dirs():
            for suffix in (".json",):
                path = directory / f"{workflow_id}{suffix}"
                if path.is_file():
                    return self._read_template(path)
            path = directory / workflow_id
            if path.is_file():
                return self._read_template(path)
        logger.warning("ComfyUI workflow template not found: %s", workflow_id)
        return None

    @staticmethod
    def _read_template(path: Path) -> Optional[Dict[str, Any]]:
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and "prompt" in data:
                data = data["prompt"]
            return data if isinstance(data, dict) else None
        except (OSError, ValueError) as exc:
            logger.error("Failed to read ComfyUI workflow template %s: %s", path, exc)
            return None

    def _list_local_workflow_templates(self) -> List[Dict[str, Any]]:
        workflows: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for directory in self._template_dirs():
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                wf_id = path.stem
                if wf_id in seen:
                    continue
                seen.add(wf_id)
                name = wf_id
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data.get("name"):
                        name = data["name"]
                except (OSError, ValueError):
                    pass
                workflows.append(
                    {
                        "id": wf_id,
                        "name": name,
                        "kind": self._infer_workflow_kind(wf_id),
                        "local": True,
                    }
                )
        return workflows

    @staticmethod
    def _infer_workflow_kind(workflow_id: str) -> str:
        lowered = workflow_id.lower()
        if any(key in lowered for key in ("t2v", "i2v", "r2v", "视频", "video", "animate", "动作")):
            return "video"
        if any(key in lowered for key in ("tts", "audio", "声音", "fish", "voice")):
            return "audio"
        return "image"

    @staticmethod
    def _apply_input_values(
        workflow: Dict[str, Any],
        parameters: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Apply ``"NODE_ID:field"`` / ``"NODE_ID.field"`` parameters onto a workflow."""
        if not parameters:
            return workflow
        prompt = json.loads(json.dumps(workflow))  # deep copy
        for raw_key, value in parameters.items():
            key = str(raw_key)
            if key in prompt and isinstance(value, dict):
                node_inputs = prompt[key].setdefault("inputs", {})
                if isinstance(node_inputs, dict):
                    node_inputs.update(value)
                continue
            separator = ":" if ":" in key else ("." if "." in key else "")
            if not separator:
                # Bare widget key (e.g. "seed", "size", "duration"): apply to
                # the node whose inputs contain exactly one matching field.
                matches = [
                    node_id
                    for node_id, node in prompt.items()
                    if isinstance(node, dict)
                    and isinstance(node.get("inputs"), dict)
                    and key in node["inputs"]
                ]
                if len(matches) == 1:
                    prompt[matches[0]]["inputs"][key] = value
                elif len(matches) > 1:
                    logger.warning(
                        "ComfyUI parameter %r matches multiple nodes %s; "
                        "use '<node_id>:<field>' to disambiguate",
                        key,
                        matches,
                    )
                else:
                    logger.warning(
                        "ComfyUI workflow has no node field matching parameter %r",
                        key,
                    )
                continue
            node_id, _, field = key.partition(separator)
            node = prompt.get(node_id)
            if not isinstance(node, dict):
                logger.warning("ComfyUI workflow has no node %r for parameter %r", node_id, key)
                continue
            node_inputs = node.setdefault("inputs", {})
            if isinstance(node_inputs, dict):
                node_inputs[field] = value
        return prompt

    @staticmethod
    def _inject_uploaded_files(
        workflow: Dict[str, Any],
        upload_results: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Wire uploaded file names into the workflow nodes for standard mode.

        ``upload_files`` is keyed by the target node id (``{"5": local_path}``);
        after upload we set that node's image/video/audio input to the filename.
        """
        prompt = json.loads(json.dumps(workflow))
        for node_id, uploaded in upload_results.items():
            node = prompt.get(node_id)
            if not isinstance(node, dict):
                logger.warning(
                    "ComfyUI workflow has no node %r for uploaded file", node_id
                )
                continue
            inputs = node.setdefault("inputs", {})
            if not isinstance(inputs, dict):
                continue
            filename = uploaded.get("filename") or ""
            field = next(
                (f for f in ("image", "images", "video", "audio") if f in inputs),
                "image",
            )
            if field == "images":
                inputs[field] = [filename]
            else:
                inputs[field] = filename
        return prompt

    def _build_standard_results(self, entry: Dict[str, Any]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        outputs = entry.get("outputs", {})
        for node_id, node_out in outputs.items():
            if not isinstance(node_out, dict):
                continue
            for media_key, media_type in (
                ("images", "image"),
                ("videos", "video"),
                ("gifs", "video"),
            ):
                for item in node_out.get(media_key, []):
                    if isinstance(item, dict):
                        results.append(
                            {
                                "type": media_type,
                                "url": "",
                                "raw": item,
                                "node_id": node_id,
                            }
                        )
        return results
