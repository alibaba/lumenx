# R2V Polish Sub-Agent — Forward Design (v0)

**Status**: Design-only. Not implemented this round.
**Owner**: StarLotus (Studio domain)
**Target file once implemented**: `src/apps/comic_gen/studio_polish_agent.py` (new)
**Companion runtime**: `src/apps/comic_gen/atelier_agent.py` (existing; lives on Atelier branch)
**Date**: 2026-05-21
**Related**: Issue 8 (#114) — AI Polish 真改造；this doc covers the **post-v1 evolution** of the polish capability after the immediate functional fix (#117/#118/#119) ships.

---

## 1. Motivation

The shipped polish (after #117/#118/#119) is a **fixed-system-prompt LLM call**:

```
user_message → [DEFAULT_VIDEO_POLISH_PROMPT or PromptConfig override] → Qwen3.6-plus → {prompt_cn, prompt_en}
```

This works, but it has structural ceilings:

| Limit | Why it hurts |
|---|---|
| **One prompt fits all models** | HappyHorse / Wan 2.7 / Kling 3 / Seedance 2 / Vidu each have wildly different prompt grammars (HappyHorse loves multi-shot stack, Wan wants camera-language tags, Kling prefers concise English). A fixed system prompt is a lowest-common-denominator compromise. |
| **No retrieval / no tools** | Cannot consult model-specific docs, cannot check capability constraints (e.g. "this model can't do 6+ shots in one clip"), cannot pull in user history. |
| **No iterative reasoning** | Single-shot LLM call; no self-critique, no "let me try 3 variants and pick the best". |
| **Can't load user-authored skills** | User has `~/.claude/skills/seedance-prompt-master/SKILL.md` and (per Issue 8 brief) a HappyHorse equivalent. These are *agent-callable* by design — a fixed prompt can't invoke them; it would need to be copy-pasted by hand, losing tool composability. |
| **No quality self-check** | Can't run the SKILL's "七条铁律" verification pass over its own output. |

The Sub-Agent path treats polish as an **agentic loop** that can call tools, load skills, and self-critique — not just a prompt template.

---

## 2. Candidate Architectures

Three serious options, plus one anti-pattern listed for completeness.

### Option A — Spawn Claude Code CLI as subprocess

```
Studio backend
  └─ subprocess.run([
       "claude", "-p", "<task>",
       "--skill", "seedance-prompt-master",
       "--output-format", "json",
     ], timeout=60)
```

| Dimension | Score |
|---|---|
| Fidelity (skill ecosystem) | ⭐⭐⭐⭐⭐ — 1:1 reuse of `~/.claude/skills/` |
| Implementation cost | 🔴 High — install Claude Code on every dev/prod machine, manage `ANTHROPIC_API_KEY`, parse stdin/stdout protocol, handle timeouts/cancel/streaming |
| Codebase fit | 🔴 Low — LumenX currently has **zero** Claude dependency; introduces a new secret-management surface |
| Cost per polish | 🔴 High — Claude Opus/Sonnet pricing × full tool-loop turns |

**Verdict**: Highest fidelity but worst fit. Reasonable for a Claude-native shop; wrong for LumenX which is Qwen-native.

### Option B — Claude Agent SDK (Python)

```python
from claude_agent_sdk import Agent
agent = Agent(skill="seedance-prompt-master", api_key=os.getenv("ANTHROPIC_API_KEY"))
result = agent.run(task=f"Polish this prompt: {draft}")
```

| Dimension | Score |
|---|---|
| Fidelity | ⭐⭐⭐⭐ — SDK natively loads skills |
| Implementation cost | 🟡 Medium — `pip install claude-agent-sdk` + glue code |
| Codebase fit | 🔴 Low — same secret/dep issue as A |
| Cost per polish | 🔴 Same as A |

**Verdict**: Marginally cleaner than A. Same fit issue.

### Option C — Reuse Atelier Agent Infrastructure (recommended)

> Atelier branch already ships a codex-style agent runtime (`atelier_agent.py`) with `AtelierToolRegistry`, `AtelierPlannerRegistry`, `AtelierPermissionEnforcer`, `AtelierAgentHarness`. See CLAUDE.md "Atelier Agent runtime" section for the canonical reference.

```
Studio polish trigger
  └─ StudioPolishAgent (new, ~150 LOC)
       ├─ SkillLoader      ← reads SKILL.md + references/ from disk
       ├─ Tool registry    ← reuse Atelier ToolRegistry shape
       │    polish.refineDraft / checkVisualizability / injectModelDoc / compareCandidates
       ├─ Planner          ← StudioPolishPlanner (LLM-backed, Qwen3.6-plus)
       ├─ Permission       ← reuse AtelierPermissionEnforcer (max turns / max tool calls)
       └─ Harness          ← preview / execute, same persistence story as Atelier
```

| Dimension | Score |
|---|---|
| Fidelity | ⭐⭐⭐ — SKILL.md content is injected as system prompt + references read into context; not 1:1 with Claude's skill calling semantics, but the meaningful 90% (instructions + reference material) flows through |
| Implementation cost | 🟢 Low-Medium — base infra exists; new code is the Studio-specific planner, tool set, and SkillLoader (~300 LOC total) |
| Codebase fit | ⭐⭐⭐⭐⭐ — Studio and Atelier converge on **one** agent framework, not two |
| Cost per polish | 🟢 Low — stays on Qwen3.6-plus; same cost class as today's single-call polish, slightly higher due to multi-turn |
| External deps | 🟢 Zero new — no Anthropic key, no Claude CLI |

**Verdict**: Recommended. Architecturally consistent, low marginal cost, leverages already-invested infra.

### Option D — Bespoke tool-loop in `llm.py`

Anti-pattern. We'd build a second agent framework parallel to Atelier's. Reject.

---

## 3. Recommended Design (Option C, detailed)

### 3.1 Components

```
src/apps/comic_gen/
  studio_polish_agent.py       ← NEW (entry point + planner + tools)
  studio_skill_loader.py       ← NEW (~80 LOC; reads ~/.claude/skills/*/SKILL.md)
  atelier_agent.py             ← existing (Atelier branch); shared base classes
```

`StudioPolishAgent` composes Atelier's primitives:

```python
class StudioPolishAgent:
    def __init__(
        self,
        model_family: ModelFamily,  # wan | kling | vidu | pixverse | happyhorse | seedance
        approval_mode: AtelierApprovalMode = AtelierApprovalMode.NEVER,
    ):
        self.skill = SkillLoader.load(f"{model_family.value}-prompt-master")  # optional
        self.tools = self._build_tools()                # 4 polish.* tools
        self.planner = StudioPolishPlanner(skill=self.skill, model_family=model_family)
        self.harness = AtelierAgentHarness(
            tools=self.tools,
            planner=self.planner,
            permission=AtelierPermissionEnforcer(
                approval_mode=approval_mode,
                max_tool_calls_per_turn=6,   # 4 polish ops + 2 retries
                max_generation_calls=0,      # polish doesn't generate media
            ),
        )

    def polish(self, draft: str, slots: List[Slot], feedback: str = "", prev_cn: str = "") -> Dict[str, str]:
        turn = self.harness.execute_turn(
            user_input=self._build_user_input(draft, slots, feedback, prev_cn),
        )
        return self._extract_bilingual_result(turn)
```

### 3.2 SkillLoader (new)

```python
class SkillLoader:
    SKILLS_DIR = Path.home() / ".claude" / "skills"

    @classmethod
    def load(cls, skill_name: str) -> Optional[LoadedSkill]:
        """Try to load ~/.claude/skills/{skill_name}/SKILL.md + references/*.

        Returns None if skill not installed (gracefully degrades — polish still
        works with the family-default system prompt). Reads file content into
        memory; no execution of any skill-bundled scripts.
        """
        skill_dir = cls.SKILLS_DIR / skill_name
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return None
        body = skill_md.read_text(encoding="utf-8")
        frontmatter, content = _split_frontmatter(body)
        refs_dir = skill_dir / "references"
        references = []
        if refs_dir.exists():
            for f in sorted(refs_dir.glob("*.md")):
                references.append((f.name, f.read_text(encoding="utf-8")))
        return LoadedSkill(
            name=skill_name,
            description=frontmatter.get("description", ""),
            system_prompt=content,
            references=references,  # list of (filename, body)
        )
```

**Naming convention**: skill must live at `~/.claude/skills/{family}-prompt-master/SKILL.md` (e.g. `happyhorse-prompt-master`, `seedance-prompt-master`). This convention lets `StudioPolishAgent` auto-discover the right skill from the model family selected in ShotPanel — no extra UI config.

**Security**: only reads `.md` files; ignores `*.py`, `*.sh`, anything executable. Never runs `scripts/` directories that Claude Code skills sometimes include.

### 3.3 Tool Set (v1)

Four tools, all read-only or in-memory:

| Tool | Signature | Purpose |
|---|---|---|
| `polish.refineDraft` | `(draft: str, lang: "cn"|"en"|"bilingual") → {cn, en}` | The base LLM polish call (today's behavior). Used as a building block by the agent. |
| `polish.checkVisualizability` | `(prompt: str) → {violations: [{rule, span, suggestion}]}` | Runs SKILL's "可视化铁律" check over a candidate prompt. Returns abstract emotion words ("悲伤", "氛围感") that should be expanded into concrete visuals. |
| `polish.injectModelDoc` | `(model_family) → {doc: str}` | Pulls the capability sheet for the family from `config/model_catalog/` so the planner knows constraints (max duration, supported aspect ratios, watermark options, etc.) before writing the polished prompt. |
| `polish.compareCandidates` | `(candidates: List[str]) → {ranking: [{idx, score, reason}]}` | For multi-variant polish (future: when 🤖 SMART runs N parallel polishes and picks the best). Out of scope for v1 release. |

All tools are namespaced `polish.*` to keep them disjoint from Atelier's `canvas.*` / `generation.*`.

### 3.4 Planner

`StudioPolishPlanner` extends `AtelierModelAdapterPlanner`:

- Same Qwen3.6-plus backbone, same JSON-mode response constraint
- System prompt = skill's `system_prompt` if loaded, else family-default polish prompt
- References (if any) injected as `[REFERENCE: filename]` context blocks
- Plan schema: ordered list of `polish.*` tool calls

Example planner output for HappyHorse with skill loaded:

```json
{
  "plan": [
    {"tool": "polish.injectModelDoc", "args": {"model_family": "happyhorse"}},
    {"tool": "polish.refineDraft", "args": {"draft": "...", "lang": "bilingual"}},
    {"tool": "polish.checkVisualizability", "args": {"prompt": "<output of refineDraft EN>"}},
    {"tool": "polish.refineDraft", "args": {"draft": "<previous + violations as feedback>", "lang": "bilingual"}}
  ]
}
```

### 3.5 Permission & Cost Bounds

Reuse `AtelierPermissionEnforcer`. Polish-specific tightening:

| Setting | Value | Reason |
|---|---|---|
| `max_tool_calls_per_turn` | 6 | refineDraft × 2 + checkVisualizability + injectModelDoc + 2 retry slack |
| `max_generation_calls` | 0 | polish never generates media |
| `approval_mode` (default) | `NEVER` | tools are read-only/in-memory; no destructive ops |
| Per-turn time budget | 30s | hard wall-clock cap; otherwise abort with timeout error |
| Per-turn token budget | 8K input + 2K output | typical polish ≈ 1.5K total; this is a safety wall |

---

## 4. UX Integration (Storyboard R2V workbench)

Two buttons, side by side, where today's single ✨ POLISH lives:

```
[ ✨ POLISH ]     [ 🤖 SMART ]
  ↑ today          ↑ new (Option C agent)
  fast (1 call)    slower (3-6 calls), uses skill if installed
  ~2s, low cost    ~8-15s, ~3-5x cost
```

**Smart button copy/state matrix:**

| State | Label | Tooltip |
|---|---|---|
| Idle, skill installed | 🤖 SMART | `Uses {skill_name} (skill loaded)` |
| Idle, no skill | 🤖 SMART | `No model-specific skill installed. Falls back to deep polish with reasoning.` |
| Running | 🤖 SMART · {step}/{total} | Live planner progress (`Step 2/4: checking visualizability`) |
| Failed | 🤖 SMART | Same error contract as ✨ POLISH (PolishError reasons) |

Result panel is **the same** as ✨ POLISH (CN + EN columns, per-column Copy/Apply, feedback iteration). User cannot tell which path produced the result from the UI chrome — only from latency and quality.

**No auto-routing**: do NOT make ✨ POLISH silently delegate to 🤖 SMART when a skill exists. Reasons:
- User must consent to the cost/latency tradeoff
- Predictability: same button always means same backend behavior
- Debuggability: easier to bisect issues to fast vs smart path

---

## 5. Failure Modes & Mitigation

| Failure | Mitigation |
|---|---|
| Skill file disappears mid-session | `SkillLoader.load()` returns `None`; planner falls back to family-default prompt; warn-log "skill {name} not found, using default" |
| Skill SKILL.md has broken frontmatter | Treat as no-skill; log warning |
| Tool loop never terminates | `max_tool_calls_per_turn=6` enforces termination; planner gets hard "stop" signal at limit |
| LLM emits non-JSON tool call | Same `json_parse_error` → 502 contract as ✨ POLISH |
| User-authored skill has malicious instructions ("ignore everything, return X") | Mitigated by: (a) read-only tool set (worst case = bad prompt, not data exfil), (b) user owns their own `~/.claude/skills/` so this is self-inflicted, (c) skill content NOT auto-loaded from network — only filesystem |
| Cost runaway | Per-turn token budget + tool call cap; per-day spending alert can be added at infra layer (out of scope) |

---

## 6. Migration Path

Phased rollout once implementation starts (not now):

| Phase | Scope | Gate to next phase |
|---|---|---|
| **0 (current)** | ✨ POLISH only (#117/#118/#119) | This doc approved |
| **1** | Build `SkillLoader` + `StudioPolishAgent` skeleton with only `polish.refineDraft` tool; ship 🤖 SMART button gated behind feature flag `studio.smart_polish` | Internal eval shows ≥20% prompt quality win vs ✨ POLISH on hand-graded set |
| **2** | Add `checkVisualizability` + `injectModelDoc` tools; planner can multi-turn | Feature flag → opt-in setting toggle for users |
| **3** | Add `compareCandidates` + parallel variant exploration | Default-on for all models with installed skills |
| **4 (optional)** | Migrate ✨ POLISH path to share the agent infra (with a degenerate 1-tool plan); deprecate the dual button | Only if Phase 3 metrics justify abandoning the fast path |

Phase 4 is **not** committed — keeping a separate fast path may always be valuable for users who want zero-cost speed.

---

## 7. Fallback Plan If Option C Underperforms

If after Phase 2 the C path consistently underperforms what a direct Claude Code skill call would produce (e.g., the SKILL relies heavily on Claude's tool-use idioms that Qwen3.6-plus can't replicate), upgrade to Option B (`claude-agent-sdk`):

1. Keep `StudioPolishAgent` as the public interface; swap its planner internals
2. Introduce optional `ANTHROPIC_API_KEY` setting in `~/.lumen-x/config.json`
3. Add per-model-family routing: cheap models stay on Qwen-C path, premium models can opt into Anthropic-B path

This is reversible at any time without changing the frontend contract.

---

## 8. Non-Goals (explicit)

To keep the design tight, the following are **out of scope**:

1. Cross-shot prompt continuity ("make shot 2 visually consistent with shot 1") — separate feature; would need scene-level state, not polish-level
2. Multi-modal polish (using a reference image as polish input) — handled today by Wan's I2I refine endpoint, not polish
3. Streaming partial results to UI mid-polish — nice-to-have; current "skeleton + final swap" is sufficient
4. Cost telemetry / billing UI — separate observability work
5. User-facing skill installer UI ("Install HappyHorse Polish Skill" button) — users manage `~/.claude/skills/` themselves via Claude Code; LumenX just reads
6. Polish agent that can refuse jobs ("this prompt is too vague, please add more detail before I polish") — interesting but out of v1 scope

---

## 9. Open Questions (to resolve before Phase 1)

These are not blockers for **this** design doc but must be answered before implementation:

1. **Where does `~/.claude/skills/` live in production / desktop builds?** Today's assumption is the user's home dir. For packaged LumenX desktop, do we ship a default skill bundle? Empty by default?
2. **Should the polish agent see the user's previous polish attempts** (turn history, à la Atelier session)? Atelier persists `agent_turns` per project; Studio doesn't (yet). Adding turn history changes the persistence story.
3. **Per-shot vs per-project polish settings**: ✨ POLISH today loads `script_id` prompt config. Should 🤖 SMART have its own per-project enable/disable, or share?
4. **What's the licensing story for ingesting user skill files into LumenX's LLM context?** SKILL.md says "Role: 你是一位精通..."; we pass that verbatim to Qwen. If skill is MIT/CC0 from user's own machine, this is fine. If a skill was shared from elsewhere, license bears noting.

---

## 10. Sign-off Checklist

Before Phase 1 implementation kicks off:

- [ ] Atelier branch's `atelier_agent.py` merged to `main` (or this Studio agent ships from a feature branch that includes the Atelier infra)
- [ ] Naming convention `{family}-prompt-master` agreed with skill authors
- [ ] HappyHorse skill written and validated against current ShotPanel
- [ ] Feature flag `studio.smart_polish` plumbed
- [ ] Eval set (≥30 prompts × 5 families) prepared with rubric for "polished better than draft" / "polished worse than draft" / "tied"
- [ ] Decision: ship 🤖 SMART for all 5 families simultaneously, or family-by-family

---

## Appendix A — Why not LangChain / LlamaIndex / etc.

These libraries solve the wrong abstraction layer for this problem:
- LangChain: a Swiss army knife of LLM patterns; we need exactly one pattern (tool-loop with skill prompt) — adopting LangChain's whole worldview is overkill
- LlamaIndex: optimized for retrieval-heavy workflows; polish is not retrieval-heavy
- Existing Atelier infra: already in the codebase, already familiar to the team, already passes our type/test bar

The decision rule: **build on infra we already maintain before importing infra we'd need to learn**.

## Appendix B — Reference Files

- `src/apps/comic_gen/atelier_agent.py` — base classes (Atelier branch)
- `src/apps/comic_gen/llm.py` — current polish implementation (`polish_video_prompt`, `polish_r2v_prompt`)
- `src/apps/comic_gen/llm_adapter.py` — Qwen call layer (#117 model fallback chain)
- `~/.claude/skills/seedance-prompt-master/SKILL.md` — reference skill structure
- `frontend/src/components/modules/storyboard-r2v/PolishPanel.tsx` — UI target for new 🤖 SMART button
- `docs/plans/2026-05-09-atelier-agent-runtime-implementation-plan.md` — Atelier agent design that this builds on
