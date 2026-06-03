"""Atelier Reviewer Agent — guardian planner for generation calls (v1.5 #11).

When a tool with ``mutation_scope='generation_write'`` is requested AND
``policy.enable_reviewer is True``, a lightweight guardian runs three checks
before the tool executor fires:

1. **BudgetCheck** — estimated cost (from ``agentPricing`` model table;
   threshold from ``policy.max_generation_cost_per_turn``, default 5.0 CNY).
2. **DuplicateCheck** — same (prompt + model + sorted reference URLs) hash as
   an existing candidate on the target node.
3. **SafetyCheck** — keyword blocklist scan on the prompt.

The guardian returns one of ``approve / deny / escalate_to_user``:
  - **approve** — tool execution proceeds normally.
  - **deny** — tool_call is marked ``failed`` with the reviewer's reason.
  - **escalate_to_user** — emits ``tool_plan + awaiting_approval`` so the
    frontend can surface a Preview card (same wire shape as the existing
    untrusted-policy gate).

Design constraints:
  - No LLM call in v1 — deterministic checks only. The ``SafetyCheck``
    uses a small keyword blocklist. An LLM judge can be added in v2 by
    extending ``SafetyCheck`` without changing the reviewer interface.
  - The reviewer runs synchronously before the executor. Cost: negligible
    (hash + string scan).
  - ``enable_reviewer`` defaults to ``False`` so existing behavior is
    unchanged unless the user or policy explicitly opts in.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class ReviewerAction(str, Enum):
    APPROVE = "approve"
    DENY = "deny"
    ESCALATE = "escalate_to_user"


@dataclass(frozen=True)
class CheckResult:
    """Single check outcome within a reviewer pass."""
    check_name: str
    passed: bool
    reason: str = ""


@dataclass(frozen=True)
class ReviewerResult:
    """Aggregate reviewer verdict for a single tool call."""
    action: ReviewerAction
    reason: str
    checks: List[CheckResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pricing table — per-candidate estimated cost (CNY).
#
# Conservative upper bounds so false-deny is unlikely but budget_blowup
# still catches gross overuse.  Values mirror the DashScope / Kling /
# PixVerse / HappyHorse wholesale tiers as of 2026-05.  Unknown models
# default to the highest tier so the reviewer errs on the side of caution.
# ---------------------------------------------------------------------------

_MODEL_COST_CNY: Dict[str, float] = {
    "wan2.7-i2v": 0.35,
    "wan2.7-r2v": 0.50,
    "wan2.5-i2v": 0.30,
    "kling-1.6-i2v": 1.20,
    "kling-1.6-r2v": 1.50,
    "vidu-2.0-i2v": 0.80,
    "pixverse-4.5-i2v": 0.60,
    "happyhorse-1.0-i2v": 0.45,
    "happyhorse-1.0-r2v": 0.60,
    "seedance-2.0-i2v": 0.50,
}
_DEFAULT_COST_CNY = 1.50  # unknown model → assume expensive
_DEFAULT_MAX_GENERATION_COST = 5.0  # CNY per turn


def _estimate_cost(model: str, batch_size: int) -> float:
    """Return the estimated cost in CNY for ``batch_size`` candidates of
    ``model``.  Uses the pricing table above; unknown models fall back to
    the conservative default."""
    per_unit = _MODEL_COST_CNY.get(model, _DEFAULT_COST_CNY)
    return per_unit * max(1, batch_size)


# ---------------------------------------------------------------------------
# Safety blocklist (v1 — keyword-only; v2 may add an LLM judge).
# ---------------------------------------------------------------------------

_SAFETY_BLOCKLIST = frozenset([
    "nude", "naked", "explicit", "pornographic", "nsfw",
    "gore", "mutilation", "self-harm", "suicide",
    "child abuse", "terrorism", "bomb-making",
])


def _check_safety(prompt: str) -> Optional[str]:
    """Return a human-readable reason if the prompt triggers the blocklist,
    otherwise ``None``."""
    lower = prompt.lower()
    for keyword in _SAFETY_BLOCKLIST:
        if keyword in lower:
            return f"Prompt contains blocked keyword: {keyword!r}"
    return None


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def _candidate_fingerprint(prompt: str, model: str, refs: List[str]) -> str:
    """Deterministic hash of (prompt, model, sorted reference URLs) so we
    can detect duplicate generation requests on the same node."""
    parts = [prompt.strip(), model.strip()] + sorted(refs)
    raw = "\n".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _check_duplicate(
    prompt: str,
    model: str,
    reference_urls: List[str],
    existing_candidates: List[Dict[str, Any]],
) -> Optional[str]:
    """Return a reason string if a candidate with the same fingerprint
    already exists on the node, otherwise ``None``."""
    if not existing_candidates:
        return None
    target_fp = _candidate_fingerprint(prompt, model, reference_urls)
    for cand in existing_candidates:
        snapshot = cand.get("generation_snapshot") or {}
        cand_prompt = str(snapshot.get("prompt") or cand.get("prompt") or "").strip()
        cand_model = str(snapshot.get("model") or "").strip()
        cand_refs = sorted(
            str(r) for r in (snapshot.get("reference_image_urls") or [])
        )
        cand_fp = _candidate_fingerprint(cand_prompt, cand_model, cand_refs)
        if cand_fp == target_fp:
            return (
                f"Duplicate generation: a candidate with the same prompt, model, "
                f"and references already exists (candidate {cand.get('id', '?')})"
            )
    return None


# ---------------------------------------------------------------------------
# AtelierReviewer — the guardian entry point
# ---------------------------------------------------------------------------

class AtelierReviewer:
    """Synchronous guardian that screens generation_write tool calls.

    Called by ``AtelierAgentHarness.run_turn_streaming`` BEFORE the executor
    runs, only when:
      * The tool's ``mutation_scope == 'generation_write'``
      * ``policy.enable_reviewer is True``

    The review is deterministic in v1 (no LLM call). Three checks run in
    order; the first failing check determines the verdict:

    1. **SafetyCheck** — blocklist scan on the prompt. Violation → **deny**.
    2. **DuplicateCheck** — fingerprint match against existing candidates on
       the target node. Match → **deny** (avoid burning money on a repeat).
    3. **BudgetCheck** — estimated cost vs threshold. Breach → **escalate**
       (the user can still approve, but the agent can't silently overspend).

    When all three checks pass the verdict is **approve**.
    """

    def review(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        policy: Any,
        project: Any,
    ) -> ReviewerResult:
        """Screen a single tool call.

        Parameters
        ----------
        tool_name:
            Registered Atelier tool name (e.g. ``generation.createVideoCandidates``).
        arguments:
            The resolved arguments dict that would be passed to the executor.
        policy:
            ``AtelierAgentPolicy`` on the project (carries ``enable_reviewer``,
            ``max_generation_cost_per_turn``).
        project:
            ``AtelierProject`` instance (carries ``nodes`` for duplicate check).

        Returns
        -------
        ReviewerResult
            With action ``approve``, ``deny``, or ``escalate_to_user`` and
            a list of per-check outcomes.
        """
        checks: List[CheckResult] = []
        prompt = str(arguments.get("prompt") or "")
        model = str(arguments.get("model") or "wan2.7-i2v")
        batch_size = int(arguments.get("batch_size") or 3)
        reference_urls = list(arguments.get("reference_image_urls") or [])
        node_id = str(arguments.get("node_id") or "")

        # --- 1. Safety check ------------------------------------------------
        safety_reason = _check_safety(prompt)
        if safety_reason:
            checks.append(CheckResult(
                check_name="safety",
                passed=False,
                reason=safety_reason,
            ))
            return ReviewerResult(
                action=ReviewerAction.DENY,
                reason=f"Reviewer denied: {safety_reason}",
                checks=checks,
            )
        checks.append(CheckResult(check_name="safety", passed=True))

        # --- 2. Duplicate check ----------------------------------------------
        existing_candidates = self._get_existing_candidates(project, node_id)
        dup_reason = _check_duplicate(prompt, model, reference_urls, existing_candidates)
        if dup_reason:
            checks.append(CheckResult(
                check_name="duplicate_with_existing_take",
                passed=False,
                reason=dup_reason,
            ))
            return ReviewerResult(
                action=ReviewerAction.DENY,
                reason=f"Reviewer denied: {dup_reason}",
                checks=checks,
            )
        checks.append(CheckResult(check_name="duplicate_with_existing_take", passed=True))

        # --- 3. Budget check -------------------------------------------------
        threshold = float(getattr(policy, "max_generation_cost_per_turn", _DEFAULT_MAX_GENERATION_COST) or _DEFAULT_MAX_GENERATION_COST)
        estimated_cost = _estimate_cost(model, batch_size)
        if estimated_cost > threshold:
            reason = (
                f"Estimated cost {estimated_cost:.2f} CNY exceeds threshold "
                f"{threshold:.2f} CNY (model={model}, batch_size={batch_size})"
            )
            checks.append(CheckResult(
                check_name="budget_blowup",
                passed=False,
                reason=reason,
            ))
            return ReviewerResult(
                action=ReviewerAction.ESCALATE,
                reason=f"Reviewer escalated: {reason}",
                checks=checks,
            )
        checks.append(CheckResult(check_name="budget_blowup", passed=True))

        # --- All passed ------------------------------------------------------
        return ReviewerResult(
            action=ReviewerAction.APPROVE,
            reason="All reviewer checks passed",
            checks=checks,
        )

    @staticmethod
    def _get_existing_candidates(
        project: Any, node_id: str,
    ) -> List[Dict[str, Any]]:
        """Retrieve the existing candidates on the target node."""
        if not node_id or not project:
            return []
        nodes = getattr(project, "nodes", None)
        if not nodes:
            return []
        node = next((n for n in nodes if n.id == node_id), None)
        if not node:
            return []
        return list((getattr(node, "data", None) or {}).get("candidates") or [])
