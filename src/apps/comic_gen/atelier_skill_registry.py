"""Atelier Skill registry — declarative SKILL.md discovery.

v1.4 BATCH 3 (Skills layer). The registry walks a 6-stage precedence chain
and parses YAML frontmatter + freeform body out of `SKILL.md` files. Discovered
skills feed two consumers:

  * `_build_atelier_llm_prefix` (atelier_agent.py) — when an agent turn carries
    a `skill_name`, the matching SkillSpec's `prompt_template` + body are
    spliced into the system prompt directive section so the LLM sees the
    skill's instructions inline.
  * the frontend's empty-state skill catalog (AgentPanelV3) — `GET
    /atelier/skills` returns the discovered list so the 8 cards in the
    rail can be data-driven instead of hard-coded.

Precedence (later stages override earlier by `name`):

  1. bundled   — src/apps/comic_gen/skills/<name>/SKILL.md (ships with code)
  2. managed   — ~/.lumen-x/skills/<name>/SKILL.md   (system-installed packs)
  3. personal  — ~/.atelier/skills/<name>/SKILL.md   (user global library)
  4. project   — <atelier_projects.json dir>/skills/<name>/SKILL.md
  5. workspace — <repo_root>/.atelier/skills/<name>/SKILL.md
  6. extraDirs — colon-separated `ATELIER_SKILL_EXTRA_DIRS` env

Frontmatter contract:
  Required: name, description, prompt_template
  Optional: expected_tools (List[str]), default_iteration_cap (int 1..10),
            requires_inputs (List[str]), category (str), icon (str),
            title (str), subtitle (str)

Body after the closing `---` is freeform markdown fed verbatim to the LLM.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Env knobs ----------------------------------------------------------------
ATELIER_SKILL_PERSONAL_DIR_ENV = "ATELIER_SKILL_PERSONAL_DIR"
ATELIER_SKILL_MANAGED_DIR_ENV = "ATELIER_SKILL_MANAGED_DIR"
ATELIER_SKILL_PROJECT_DIR_ENV = "ATELIER_SKILL_PROJECT_DIR"
ATELIER_SKILL_WORKSPACE_DIR_ENV = "ATELIER_SKILL_WORKSPACE_DIR"
ATELIER_SKILL_EXTRA_DIRS_ENV = "ATELIER_SKILL_EXTRA_DIRS"
ATELIER_SKILL_REGISTRY_CACHE_ENV = "ATELIER_SKILL_REGISTRY_CACHE"

_DEFAULT_CACHE_TTL_SECONDS = 5.0
_VALID_ITERATION_CAP_RANGE = (1, 10)


@dataclass(frozen=True)
class SkillSpec:
    """One discovered skill. Returned from `discover_skills()` and consumed
    by both the agent prompt builder and the frontend skill catalog route.
    """

    name: str
    description: str
    prompt_template: str
    body: str = ""
    expected_tools: Tuple[str, ...] = field(default_factory=tuple)
    default_iteration_cap: Optional[int] = None
    requires_inputs: Tuple[str, ...] = field(default_factory=tuple)
    category: Optional[str] = None
    icon: Optional[str] = None
    title: Optional[str] = None
    subtitle: Optional[str] = None
    source_stage: str = "bundled"
    source_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "prompt_template": self.prompt_template,
            "body": self.body,
            "expected_tools": list(self.expected_tools),
            "default_iteration_cap": self.default_iteration_cap,
            "requires_inputs": list(self.requires_inputs),
            "category": self.category,
            "icon": self.icon,
            "title": self.title,
            "subtitle": self.subtitle,
            "source_stage": self.source_stage,
            "source_path": self.source_path,
        }


# --- precedence-chain plumbing -------------------------------------------

def _bundled_skills_dir() -> Path:
    """The bundled SKILL.md library that ships with the comic_gen app."""
    return Path(__file__).resolve().parent / "skills"


def _managed_skills_dir() -> Path:
    override = os.getenv(ATELIER_SKILL_MANAGED_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".lumen-x" / "skills"


def _personal_skills_dir() -> Path:
    override = os.getenv(ATELIER_SKILL_PERSONAL_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".atelier" / "skills"


def _project_skills_dir(atelier_data_file: Optional[str] = None) -> Optional[Path]:
    """The per-project skills folder lives next to the
    `atelier_projects.json` file the pipeline persists. Tests override
    this via the env shim so a fixture tmp_path can host project-stage
    skills.
    """
    override = os.getenv(ATELIER_SKILL_PROJECT_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if atelier_data_file:
        return Path(atelier_data_file).resolve().parent / "skills"
    return None


def _workspace_skills_dir(repo_root: Optional[str] = None) -> Optional[Path]:
    """The workspace stage is the team-shared, version-controlled folder.
    Defaults to `<repo_root>/.atelier/skills`. Tests override via env.
    """
    override = os.getenv(ATELIER_SKILL_WORKSPACE_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if repo_root:
        return Path(repo_root).resolve() / ".atelier" / "skills"
    return None


def _extra_dirs() -> List[Path]:
    raw = os.getenv(ATELIER_SKILL_EXTRA_DIRS_ENV) or ""
    if not raw.strip():
        return []
    parts = [p.strip() for p in raw.split(os.pathsep) if p.strip()]
    return [Path(p).expanduser() for p in parts]


def _registry_cache_ttl() -> float:
    raw = os.getenv(ATELIER_SKILL_REGISTRY_CACHE_ENV)
    if not raw:
        return _DEFAULT_CACHE_TTL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_CACHE_TTL_SECONDS


# --- frontmatter parser --------------------------------------------------

def _split_frontmatter(text: str) -> Tuple[str, str]:
    """Return (frontmatter_yaml, body) from a SKILL.md document.

    The document MUST start with a `---` fence. Empty frontmatter yields
    ("", body). Missing fence yields ("", text) so callers can soft-skip.
    """
    if not text.startswith("---"):
        return "", text
    # Walk character-by-character; first newline-after-fence is the
    # boundary. Then find the closing `---` on its own line.
    after_first = text[3:]
    if after_first.startswith("\n"):
        after_first = after_first[1:]
    elif after_first.startswith("\r\n"):
        after_first = after_first[2:]
    closing = after_first.find("\n---")
    if closing == -1:
        return "", text  # malformed — caller skips
    fm = after_first[:closing]
    body = after_first[closing + len("\n---") :]
    if body.startswith("\n"):
        body = body[1:]
    elif body.startswith("\r\n"):
        body = body[2:]
    return fm, body


def _coerce_iteration_cap(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    lo, hi = _VALID_ITERATION_CAP_RANGE
    if n < lo or n > hi:
        return None
    return n


def _coerce_str_tuple(raw: Any) -> Tuple[str, ...]:
    if not raw:
        return tuple()
    if isinstance(raw, (list, tuple)):
        return tuple(str(item) for item in raw if isinstance(item, (str, int, float)))
    if isinstance(raw, str):
        return (raw,)
    return tuple()


def _parse_skill_md(skill_dir_name: str, path: Path, source_stage: str) -> Optional[SkillSpec]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("atelier_skill_registry: cannot read %s: %s", path, exc)
        return None
    fm_text, body = _split_frontmatter(text)
    if not fm_text.strip():
        logger.warning("atelier_skill_registry: missing frontmatter in %s", path)
        return None
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("atelier_skill_registry: PyYAML unavailable: %s", exc)
        return None
    try:
        data = yaml.safe_load(fm_text) or {}
    except Exception as exc:
        logger.warning("atelier_skill_registry: yaml parse failed for %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("atelier_skill_registry: frontmatter must be a mapping in %s", path)
        return None

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        logger.warning("atelier_skill_registry: missing 'name' in %s", path)
        return None
    name = name.strip()
    if name != skill_dir_name:
        logger.warning(
            "atelier_skill_registry: skill name '%s' does not match parent dir '%s' (%s)",
            name, skill_dir_name, path,
        )
        # tolerate but use the directory name for canonical lookup
        name = skill_dir_name

    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        logger.warning("atelier_skill_registry: missing 'description' in %s", path)
        return None

    prompt_template = data.get("prompt_template")
    if not isinstance(prompt_template, str) or not prompt_template.strip():
        logger.warning("atelier_skill_registry: missing 'prompt_template' in %s", path)
        return None

    return SkillSpec(
        name=name,
        description=description.strip(),
        prompt_template=prompt_template.rstrip(),
        body=body.strip("\n"),
        expected_tools=_coerce_str_tuple(data.get("expected_tools")),
        default_iteration_cap=_coerce_iteration_cap(data.get("default_iteration_cap")),
        requires_inputs=_coerce_str_tuple(data.get("requires_inputs")),
        category=data.get("category") if isinstance(data.get("category"), str) else None,
        icon=data.get("icon") if isinstance(data.get("icon"), str) else None,
        title=data.get("title") if isinstance(data.get("title"), str) else None,
        subtitle=data.get("subtitle") if isinstance(data.get("subtitle"), str) else None,
        source_stage=source_stage,
        source_path=str(path),
    )


def _walk_stage(directory: Optional[Path], source_stage: str) -> Dict[str, SkillSpec]:
    if directory is None or not directory.exists() or not directory.is_dir():
        return {}
    out: Dict[str, SkillSpec] = {}
    try:
        for child in sorted(directory.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.exists():
                continue
            spec = _parse_skill_md(child.name, skill_md, source_stage)
            if spec is None:
                continue
            out[spec.name] = spec
    except OSError as exc:
        logger.warning("atelier_skill_registry: cannot walk %s: %s", directory, exc)
    return out


# --- public registry -----------------------------------------------------

# Module-level cache. Keyed by (stage_paths) signature so test fixtures that
# rotate env vars don't see each other's state. Reset on every `discover_skills`
# call when the cache TTL elapses.
_REGISTRY_CACHE: Dict[str, Tuple[float, List[SkillSpec]]] = {}


def _cache_key(
    *,
    atelier_data_file: Optional[str],
    repo_root: Optional[str],
) -> str:
    parts = [
        str(_bundled_skills_dir()),
        str(_managed_skills_dir()),
        str(_personal_skills_dir()),
        str(_project_skills_dir(atelier_data_file)),
        str(_workspace_skills_dir(repo_root)),
        os.pathsep.join(str(p) for p in _extra_dirs()),
    ]
    return "|".join(parts)


def discover_skills(
    *,
    atelier_data_file: Optional[str] = None,
    repo_root: Optional[str] = None,
    use_cache: bool = True,
) -> List[SkillSpec]:
    """Walk the precedence chain and return the merged SkillSpec list.

    Later stages override earlier stages by `name`. The result is sorted
    by `name` for deterministic ordering across calls.
    """
    cache_ttl = _registry_cache_ttl()
    cache_key = _cache_key(atelier_data_file=atelier_data_file, repo_root=repo_root)
    now = time.time()
    if use_cache and cache_ttl > 0:
        cached = _REGISTRY_CACHE.get(cache_key)
        if cached is not None and (now - cached[0]) < cache_ttl:
            return list(cached[1])

    merged: Dict[str, SkillSpec] = {}
    # Stage order: each later stage overrides earlier ones by name.
    merged.update(_walk_stage(_bundled_skills_dir(), "bundled"))
    merged.update(_walk_stage(_managed_skills_dir(), "managed"))
    merged.update(_walk_stage(_personal_skills_dir(), "personal"))
    merged.update(_walk_stage(_project_skills_dir(atelier_data_file), "project"))
    merged.update(_walk_stage(_workspace_skills_dir(repo_root), "workspace"))
    for extra in _extra_dirs():
        merged.update(_walk_stage(extra, "extra"))

    result = sorted(merged.values(), key=lambda spec: spec.name)
    if cache_ttl > 0:
        _REGISTRY_CACHE[cache_key] = (now, list(result))
    return result


def get_skill(
    name: str,
    *,
    atelier_data_file: Optional[str] = None,
    repo_root: Optional[str] = None,
) -> Optional[SkillSpec]:
    if not name:
        return None
    for spec in discover_skills(
        atelier_data_file=atelier_data_file, repo_root=repo_root
    ):
        if spec.name == name:
            return spec
    return None


def reset_registry_cache() -> None:
    """Drop the module-level cache. Tests call this between fixtures to
    guarantee a fresh walk of the precedence chain."""
    _REGISTRY_CACHE.clear()


def expand_prompt_template(template: str, variables: Dict[str, Any]) -> str:
    """Best-effort `{placeholder}` substitution.

    Missing keys leave the literal `{key}` in place so the LLM can still
    read it as a hint instead of crashing on a KeyError. Mirrors the
    frontend `expandTemplate` helper in atelierStore.
    """
    if not template:
        return ""
    out = template
    for key, value in variables.items():
        if value is None:
            continue
        out = out.replace("{" + key + "}", str(value))
    return out
