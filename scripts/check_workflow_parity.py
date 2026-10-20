#!/usr/bin/env python3
"""Check behavioral parity between the Claude/Codex mirrored workflow docs.

`.claude/commands/lumenx-*.md` (zh) and `.codex/workflows/lumenx-*.md` (en)
are intentionally written in different languages, so prose is expected to
differ. What must NOT drift silently is the behavioral surface:

- fenced code blocks (commands the agent will actually run)
- inline-code tokens (paths, field names, branch prefixes, model families...)

This script compares exactly those two signals per mirror pair. Divergences
that are confirmed as intentional go into WAIVERS below with a reason, so
future drift is still surfaced.

Usage:
    python3 scripts/check_workflow_parity.py

Exit code 0 = parity holds (modulo recorded waivers), 1 = drift detected.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_DIR = REPO_ROOT / ".claude" / "commands"
CODEX_DIR = REPO_ROOT / ".codex" / "workflows"

PAIR_NAMES = [
    "lumenx-build",
    "lumenx-git-publish",
    "lumenx-model-onboarding",
]

# Confirmed intentional divergences. Keyed by pair name; each entry maps an
# inline-code token to the side it is allowed to appear on exclusively,
# plus a human-readable reason. Anything not listed here is reported.
WAIVERS: dict[str, dict[str, tuple[str, str]]] = {
    # token: (allowed_side, reason) where allowed_side is "claude" or "codex"
}

FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


def strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5 :]
    return text


def strip_trailing_comment(line: str) -> str:
    # Drop annotation comments like `dist_mac/... .app   # macOS 应用` but
    # never touch a `#` that sits inside quotes (e.g. git grep patterns).
    idx = line.find(" #")
    while idx != -1:
        prefix = line[:idx]
        if prefix.count('"') % 2 == 0 and prefix.count("'") % 2 == 0:
            return prefix.rstrip()
        idx = line.find(" #", idx + 1)
    return line


def extract_code_blocks(text: str) -> list[str]:
    blocks = []
    for match in FENCE_RE.finditer(text):
        body = match.group(1).strip()
        # Normalize whitespace and drop annotation comments, keep line structure.
        normalized = "\n".join(
            " ".join(strip_trailing_comment(line).split())
            for line in body.splitlines()
        )
        blocks.append(normalized)
    return blocks


def normalize_token(token: str) -> str:
    # `feature/*` and `feature/` describe the same branch prefix.
    if token.endswith("/*"):
        return token[:-1]
    return token


def extract_inline_tokens(text: str) -> set[str]:
    # Remove fenced blocks first so their contents are not double counted.
    without_fences = FENCE_RE.sub("", text)
    return {
        normalize_token(m.group(1).strip())
        for m in INLINE_CODE_RE.finditer(without_fences)
    }


def check_pair(name: str) -> list[str]:
    claude_path = CLAUDE_DIR / f"{name}.md"
    codex_path = CODEX_DIR / f"{name}.md"
    problems: list[str] = []

    for path in (claude_path, codex_path):
        if not path.exists():
            problems.append(f"missing file: {path.relative_to(REPO_ROOT)}")
    if problems:
        return problems

    claude_text = strip_frontmatter(claude_path.read_text(encoding="utf-8"))
    codex_text = strip_frontmatter(codex_path.read_text(encoding="utf-8"))

    # 1. Fenced code blocks must match in content and order.
    claude_blocks = extract_code_blocks(claude_text)
    codex_blocks = extract_code_blocks(codex_text)
    if claude_blocks != codex_blocks:
        if sorted(claude_blocks) == sorted(codex_blocks):
            problems.append("code blocks match but appear in different order")
        else:
            claude_only = [b for b in claude_blocks if b not in codex_blocks]
            codex_only = [b for b in codex_blocks if b not in claude_blocks]
            for block in claude_only:
                problems.append(f"code block only in .claude side:\n      {block!r}")
            for block in codex_only:
                problems.append(f"code block only in .codex side:\n      {block!r}")

    # 2. Inline-code tokens (paths, fields, prefixes...) must match as a set.
    #    A token missing as inline code still counts as present if it appears
    #    verbatim in the other side's prose (languages differ, markup may too).
    waivers = WAIVERS.get(name, {})
    claude_tokens = extract_inline_tokens(claude_text)
    codex_tokens = extract_inline_tokens(codex_text)
    for token in sorted(claude_tokens - codex_tokens):
        side, _reason = waivers.get(token, (None, None))
        if side != "claude" and token not in codex_text:
            problems.append(f"inline token only in .claude side: `{token}`")
    for token in sorted(codex_tokens - claude_tokens):
        side, _reason = waivers.get(token, (None, None))
        if side != "codex" and token not in claude_text:
            problems.append(f"inline token only in .codex side: `{token}`")

    return problems


def main() -> int:
    failed = False
    for name in PAIR_NAMES:
        problems = check_pair(name)
        if problems:
            failed = True
            print(f"[DRIFT] {name}")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"[OK]    {name}")
    if failed:
        print(
            "\nParity drift detected. Sync the mirrored files, or record an"
            " intentional divergence in WAIVERS inside this script."
        )
        return 1
    print("\nAll workflow mirror pairs are behaviorally in parity.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
