#!/usr/bin/env python3
"""Check the public LumenX specification and its local links; never import the app."""

import argparse
import json
import re
from pathlib import Path
from urllib.parse import unquote


DOC_DIR = Path("docs/agents/context/lumenx-first-principles")
PHASE_STATES = {"就绪", "待前置", "等待基线", "进行中", "待现场", "需复核", "已验证"}
LINK = re.compile(r"\[[^\]\n]+\]\(([^)\n]+)\)")


def numbered_refs(text, prefix):
    """Recognize F01-F03, F01–03 and individual SJ-01 style references."""
    pattern = rf"(?<![A-Za-z]){re.escape(prefix)}(\d{{2}})(?:\s*[–—-]\s*(?:{re.escape(prefix)})?(\d{{2}}))?"
    result = set()
    for start, end in re.findall(pattern, text):
        first, last = int(start), int(end or start)
        if last < first:
            result.add(f"INVALID-RANGE:{prefix}{start}-{end}")
        else:
            result.update(f"{prefix}{number:02d}" for number in range(first, last + 1))
    return result


def check(root):
    folder = root / DOC_DIR
    errors = []
    counts = {"managed_links": 0, "source_links": 0}
    current = sorted(folder.glob("*.md")) + sorted((folder / "adr").glob("*.md"))
    texts = {p: p.read_text(encoding="utf-8") for p in current}

    def read(name):
        path = folder / name
        if path not in texts:
            errors.append(f"Missing required document: {name}")
        return texts.get(path, "")

    def unique(values, label):
        if len(values) != len(set(values)):
            errors.append(f"Duplicate {label}")
        return set(values)

    index = read("README.md")
    plan = read("REFACTOR-PLAN.md")
    ledger = read("EXECUTION-STATUS.md")
    journey = read("PRODUCT-JOURNEYS.md")
    matrix = read("TEST-MATRIX.md")
    history = read("HISTORICAL-FINDINGS.md")
    for name in ["PRODUCT-CONTRACT.md", "CONTEXT.md", "ARCHITECTURE-DRAFT.md"]:
        read(name)

    indexed = {unquote(ref.split("#", 1)[0]) for ref in LINK.findall(index)}
    for path in current:
        relative = path.relative_to(folder).as_posix()
        if relative not in indexed:
            errors.append(f"Current document missing from README registry: {relative}")

    # Public navigation must remain usable in a clean checkout. These are
    # retained instructions, not the omitted private execution archives.
    for relative in [
        "AGENTS.md",
        "CLAUDE.md",
        "docs/operations/headless.md",
        "docs/agents/deliverables/github-release-2026-10-30/README.md",
        "docs/agents/deliverables/github-release-2026-10-30/MIGRATION.md",
        "docs/agents/deliverables/github-release-2026-10-30/PLAN.md",
    ]:
        path = root / relative
        if not path.is_file():
            errors.append(f"Missing public guide: {relative}")
        else:
            texts[path] = path.read_text(encoding="utf-8")

    for path, text in texts.items():
        if text.count("```") % 2:
            errors.append(f"Unclosed code fence: {path.relative_to(root)}")
        for raw in LINK.findall(text):
            target, _, anchor = unquote(raw.strip("<>")).partition("#")
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", target) or target.startswith("mailto:"):
                continue
            target = re.sub(r":\d+$", "", target)
            dest = Path(target) if target else path
            if target and dest.is_absolute():
                errors.append(f"Nonportable absolute link: {path.name} -> {raw}")
                continue
            if target:
                dest = path.parent / dest
            dest = dest.resolve()
            if dest.is_relative_to(root.resolve() / "docs/agents"):
                counts["managed_links"] += 1
            else:
                counts["source_links"] += 1
            if not dest.is_relative_to(root.resolve()):
                errors.append(f"Link outside this checkout: {path.name} -> {raw}")
            elif not dest.is_file():
                errors.append(f"Missing material: {path.name} -> {raw}")
            elif anchor == "execution-order" and 'id="execution-order"' not in dest.read_text(encoding="utf-8"):
                errors.append(f"Missing execution-order anchor: {raw}")

    phases = unique(re.findall(r"^\| \*\*(P\d+(?:M|A)?)\b", plan, re.M), "plan phase")
    phase_rows = [line.split("|")[1:-1] for line in ledger.splitlines() if re.match(r"^\| P\d+(?:M|A)? \|", line)]
    tracked_phases = unique([row[0].strip() for row in phase_rows], "ledger phase")
    if not phases or phases != tracked_phases:
        errors.append(f"Plan/ledger phases differ: {sorted(phases ^ tracked_phases)}")
    for row in phase_rows:
        if len(row) != 4:
            errors.append(f"Malformed phase row: {row}")
            continue
        phase, state, _, evidence = (cell.strip() for cell in row)
        if state not in PHASE_STATES:
            errors.append(f"Unknown phase state: {phase}={state}")
        if state == "已验证":
            refs = LINK.findall(evidence)
            navigation = {"README.md", "PLAN.md", "MIGRATION.md"}
            if not any(
                "deliverables/" in ref
                and Path(ref.split("#", 1)[0]).name not in navigation
                for ref in refs
            ):
                errors.append(f"Verified phase requires a run record, not a navigation guide: {phase}")

    features = unique(re.findall(r"^\| (F\d{2})\b", matrix, re.M), "feature")
    scenarios = unique(re.findall(r"^\| \*\*((?:SJ|AJ|CJ)-\d{2})\b", journey, re.M), "scenario")
    journey_findings = unique(re.findall(r"^\| \*\*((?:S|A)-R\d+)\b", journey, re.M), "journey finding")
    if not features or not scenarios or not journey_findings:
        errors.append("Feature, scenario or journey finding definitions are empty")

    # The public registry preserves finding definitions and ownership, not the
    # original reports or their execution evidence. Missing definitions still
    # fail instead of silently ignoring a whole unpublished directory.
    historical_findings = unique(
        re.findall(r"^\| (ARCH:R\d+|CORE:C\d+)\b", history, re.M),
        "historical finding",
    )
    if not historical_findings:
        errors.append("Historical finding definitions are empty")
    expected_findings = journey_findings | historical_findings
    finding_rows = [line.split("|")[1:-1] for line in ledger.splitlines() if re.match(r"^\| (?:ARCH:R\d+|CORE:C\d+|[SA]-R\d+)\b", line)]
    recorded_findings = []
    for row in finding_rows:
        if len(row) != 4:
            errors.append(f"Malformed finding row: {row}")
            continue
        identity = row[0].strip().split()[0]
        recorded_findings.append(identity)
        owners = set(re.findall(r"\bP\d+(?:M|A)?\b", row[1]))
        if not owners or not owners <= phases:
            errors.append(f"Finding without valid phase owner: {identity}")
        refs = numbered_refs(row[2], "F")
        if not refs or not refs <= features:
            errors.append(f"Finding without valid feature mapping: {identity}")
    tracked_findings = unique(recorded_findings, "ledger finding")
    if tracked_findings != expected_findings:
        errors.append(f"Source/ledger findings differ: {sorted(tracked_findings ^ expected_findings)}")

    for path, text in texts.items():
        unknown = numbered_refs(text, "F") - features
        for prefix in ["SJ-", "AJ-", "CJ-"]:
            unknown |= numbered_refs(text, prefix) - scenarios
        unknown |= set(re.findall(r"\b(?:ARCH:R|CORE:C)\d+\b", text)) - historical_findings
        if unknown:
            errors.append(f"Unknown coverage references in {path.name}: {sorted(unknown)}")

    adrs = {path.name[:4]: path for path in texts if path.parent.name == "adr"}
    for path, text in texts.items():
        for identity in set(re.findall(r"ADR-(\d{4})", text)):
            if identity not in adrs:
                errors.append(f"Unknown ADR in {path.name}: {identity}")
    for identity, path in adrs.items():
        text = texts[path]
        if not re.search(r"^status: (accepted|superseded)$", text, re.M):
            errors.append(f"ADR requires an explicit decision status: {identity}")
        if not re.search(r"^reviewed: \d{4}-\d{2}-\d{2}$", text, re.M):
            errors.append(f"ADR requires a review date: {identity}")

    counts.update(documents=len(current), adrs=len(adrs), phases=len(phases), features=len(features), scenarios=len(scenarios), findings=len(tracked_findings))
    return {"scope": "document structure only; no product verification", "counts": counts, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = check(args.root.resolve())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("LumenX documents:", "FAIL" if result["errors"] else "PASS")
        print(json.dumps(result["counts"], ensure_ascii=False))
        for error in result["errors"]:
            print("-", error)
        print(result["scope"])
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
