"""分镜 markdown → 账本 draft 行：确定性 parser（纯正则，禁调 LLM）。

解析对象：C:\\Users\\YY\\剧本项目\\剧本\\第N集-*-分镜剧本.md 的 DSL：
- 文件头元信息：> **画幅**: ... | **时长**: 约 75 秒 | **场景数**: N
- 场景头：### 【Scene n】名称（对应场景卡 SC-xx）；括注内可带限定词后缀
  （如「SC-02 外」「SC-02 · 打烊后」）——SC 引用照常提取，剩余文字作为
  scene_qualifier 保留进 SB 行 data（T-B6 修复：旧正则要求括注严格只有
  引用，带限定词的整头解析失败，E07/E10/E12 共 25 镜因此丢失场景归属）
- 镜头块头：**[mm:ss.d – mm:ss.d] 标题**
- 镜头行：`[景别|运镜|简述]`（反引号包裹）
- 标注行：`[Emotion: ... | Hook: ... | Connection: ... | Sound: ...]`
- 台词：> **角色**（情绪）：台词
- 首帧插卡：（**Keyframe insert · kind**: ... PR-xx ...）

校验门禁（docs/agents/raw/2026-09-13-survey-V1-script-to-storyboard.md §3.3）：
镜号连续、时间码连续、每集时长 vs 头部声明、SC/CH/PR 引用可解析（对资产卡字典）。
解析总数与预期（179 镜 / 15 集）不符时如实报告差异，禁止硬凑。

入账语义（survey-V3 §3.5）：全部行 status=draft 幂等 upsert（fingerprint=内容联合哈希
前 16 位）；CH/SC/PR 资产卡同批入账，使 SB 的 parent_ids 引用在账本内可解析。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import Ledger  # noqa: E402
from schema import SCHEMA_VERSION  # noqa: E402

EXPECTED_EPISODES = 15
EXPECTED_SHOTS = 179

DEFAULT_SCRIPT_DIR = r"C:\Users\YY\剧本项目\剧本"
DEFAULT_DB_PATH = r"C:\Users\YY\剧本项目\ledger\ledger.db"
DEFAULT_REPORT_PATH = r"C:\Users\YY\剧本项目\ledger\import_report.json"

# ---------------------------------------------------------------------------
# 正则（全部锚定真实 DSL 样本；容错：en-dash/全角括号/全角冒号）
# ---------------------------------------------------------------------------
RE_EPISODE_TITLE = re.compile(r"^#\s*《.+?》第\s*(\d+)\s*集[「『](.+?)[」』]")
RE_META_VALUE = re.compile(r"\*\*(.+?)\*\*[：:]\s*([^|]+)")
RE_DURATION_DECL = re.compile(r"约\s*(\d+)\s*秒")
RE_SCENE_COUNT_DECL = re.compile(r"场景数\*\*[：:]\s*(\d+)")
RE_SCENE_HEADER = re.compile(
    r"^###\s*【Scene\s*(\d+)】\s*(.+?)\s*[（(]([^）)]*)[）)]"
)
RE_SHOT_HEADER = re.compile(
    r"^\*\*\[(\d{1,2}):(\d{2})(?:\.(\d+))?\s*[–—-]\s*(\d{1,2}):(\d{2})(?:\.(\d+))?\]\s*(.*?)\*\*\s*$"
)
RE_SHOT_LINE = re.compile(r"`\[([^|`]+)\|([^|`]+)\|(.+?)\]`")
RE_ANNOTATION = re.compile(r"`\[Emotion\s*[：:](.+?)\]`")
RE_DIALOGUE = re.compile(r"^>\s*\*\*(.+?)\*\*\s*(?:[（(]([^）)]*)[）)])?\s*[：:]\s*(.*)$")
RE_KEYFRAME_INSERT = re.compile(
    r"（\*\*Keyframe insert\s*·\s*([^*：:]+?)\*\*\s*[：:]\s*(.*?)）"
)
RE_CARD_HEADING = re.compile(
    r"^#\s*(Character|Scene|Prop)\s*Card\s*[—-]\s*(.+?)\s*[（(]\s*((?:CH|SC|PR)-\d+[a-z]?)\s*[)）]"
)
RE_SC_REF = re.compile(r"SC-\d+[a-z]?")
RE_PR_REF = re.compile(r"PR-\d+[a-z]?")
RE_SHOT_FILENAME = re.compile(r"^第(\d+)集-(.+?)-分镜剧本\.md$")

ANNOTATION_KEYS = ("Emotion", "Hook", "Connection", "Sound")

# 场景头括注里的"对应场景卡"前缀（标准形态），提取引用后从限定词中剥掉
_SCENE_ANNO_PREFIX = re.compile(r"^(?:对应)?\s*(?:场景卡)?\s*")


def _split_scene_annotation(anno: str) -> tuple[str, str]:
    """场景头括注 → (SC 引用, 限定词)。

    括注 = 「对应场景卡 SC-01」标准形态，或带限定词的变体（真实样本：
    「对应场景卡 SC-02 外」「SC-02 · 打烊后」「SC-02 外」）。限定词是
    有效信息（机位在场景外/时间状态），照常提取引用后原样保留。
    括注里没有 SC 引用时返回 ("", 清洗后原文)，场景块仍计数、镜头照常
    入账（scene 为空 → parent_ids 无 SC，与旧行为一致但不再整头丢失）。
    """
    anno = anno.strip()
    m = RE_SC_REF.search(anno)
    if not m:
        return "", anno
    qualifier = anno[: m.start()] + anno[m.end():]
    qualifier = _SCENE_ANNO_PREFIX.sub("", qualifier).strip(" ·・-—_")
    return m.group(0), qualifier


# ---------------------------------------------------------------------------
# 资产卡字典（引用校验的目标字典，从卡文件正则提取，不硬编码）
# ---------------------------------------------------------------------------
def load_card_dictionary(script_dir: str) -> dict:
    """扫描 asset-*-cards.md，返回 {cards: [...], name_to_char: {...}, by_id: {...}}。"""
    cards: list[dict] = []
    for fname in sorted(os.listdir(script_dir)):
        if not fname.startswith("asset-") or not fname.endswith(".md"):
            continue
        path = os.path.join(script_dir, fname)
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = RE_CARD_HEADING.match(line.strip())
                if m:
                    kind, title, cid = m.groups()
                    cards.append({
                        "id": cid,
                        "type": {"Character": "character", "Scene": "scene", "Prop": "prop"}[kind],
                        "name": title.strip(),
                    })
    by_id = {c["id"]: c for c in cards}
    name_to_char = {c["name"]: c["id"] for c in cards if c["type"] == "character"}
    return {"cards": cards, "by_id": by_id, "name_to_char": name_to_char}


# ---------------------------------------------------------------------------
# Episode 解析
# ---------------------------------------------------------------------------
def _tc_to_seconds(mins: str, secs: str, frac: Optional[str]) -> float:
    return int(mins) * 60 + int(secs) + (int(frac or 0) / 10 ** len(frac or "0"))


def _parse_annotation(inner: str) -> dict:
    fields: dict[str, str] = {}
    # RE_ANNOTATION 已剥掉 "Emotion:" 前缀，补回使其与其余 "Key: value" 段同构
    if not inner.lstrip().lower().startswith("emotion"):
        inner = "Emotion: " + inner
    for part in inner.split("|"):
        part = part.strip()
        for key in ANNOTATION_KEYS:
            if part.lower().startswith(key.lower()) and len(part) > len(key) + 1:
                fields[key] = part[len(key) + 1:].strip()
                break
    return fields


def parse_episode(path: str) -> dict:
    """解析单集 md → {meta, scenes, shots, warnings}。纯正则状态机。"""
    ep: dict[str, Any] = {
        "path": path,
        "file": os.path.basename(path),
        "episode": None,
        "title": "",
        "aspect": "",
        "duration_declared_s": None,
        "scene_count_declared": None,
        "scenes": [],
        "shots": [],
        "warnings": [],
    }
    m = RE_SHOT_FILENAME.match(ep["file"])
    if m:
        ep["episode"] = int(m.group(1))
        ep["title"] = m.group(2)

    current_scene: Optional[dict] = None
    current_shot: Optional[dict] = None

    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            stripped = line.strip()

            header_m = RE_EPISODE_TITLE.match(stripped)
            if header_m and ep["episode"] is None:
                ep["episode"] = int(header_m.group(1))
                ep["title"] = header_m.group(2)
                continue

            # 文件头元信息行只在镜头块外生效——台词行同样以 "> **" 开头，
            # 若在块内会被元信息分支吞掉（实测踩坑：E02 主管台词因此丢失）
            if stripped.startswith(">") and current_shot is None:
                meta = {k.strip(): v.strip() for k, v in RE_META_VALUE.findall(stripped)}
                if "画幅" in meta:
                    ep["aspect"] = meta["画幅"]
                if "时长" in meta:
                    dm = RE_DURATION_DECL.search(meta["时长"])
                    if dm:
                        ep["duration_declared_s"] = int(dm.group(1))
                    else:
                        ep["warnings"].append(f"L{lineno}: 时长声明无法解析: {meta['时长']}")
                if "场景数" in meta:
                    sm = re.search(r"\d+", meta["场景数"])
                    if sm:
                        ep["scene_count_declared"] = int(sm.group(0))
                continue

            scene_m = RE_SCENE_HEADER.match(stripped)
            if scene_m:
                idx, name, anno = scene_m.groups()
                sc_ref, qualifier = _split_scene_annotation(anno)
                current_scene = {
                    "index": int(idx),
                    "name": name.strip(),
                    "sc_ref": sc_ref,
                    "qualifier": qualifier,
                }
                ep["scenes"].append(current_scene)
                current_shot = None
                continue

            shot_m = RE_SHOT_HEADER.match(stripped)
            if shot_m:
                m1, s1, f1, m2, s2, f2, title = shot_m.groups()
                current_shot = {
                    "s_no": len(ep["shots"]) + 1,
                    "start": _tc_to_seconds(m1, s1, f1),
                    "end": _tc_to_seconds(m2, s2, f2),
                    "tc_raw": stripped,
                    "title": title.strip(),
                    "line_no": lineno,
                    "shot_size": "",
                    "camera_move": "",
                    "brief": "",
                    "narration": [],
                    "dialogues": [],
                    "annotation": {},
                    "keyframe_inserts": [],
                    "scene": current_scene["sc_ref"] if current_scene else "",
                    "scene_qualifier": current_scene.get("qualifier", "") if current_scene else "",
                    "text_parts": [],
                }
                ep["shots"].append(current_shot)
                continue

            if current_shot is not None:
                shot_line_m = RE_SHOT_LINE.search(stripped)
                if shot_line_m and not current_shot["shot_size"]:
                    current_shot["shot_size"], current_shot["camera_move"], current_shot["brief"] = (
                        g.strip() for g in shot_line_m.groups()
                    )
                    continue
                ann_m = RE_ANNOTATION.search(stripped)
                if ann_m and not current_shot["annotation"]:
                    current_shot["annotation"] = _parse_annotation(ann_m.group(1))
                    continue
                dial_m = RE_DIALOGUE.match(stripped)
                if dial_m:
                    speaker, emotion, text = dial_m.groups()
                    current_shot["dialogues"].append({
                        "speaker": speaker.strip(),
                        "emotion": (emotion or "").strip(),
                        "line": text.strip(),
                    })
                    continue
                kf_m = RE_KEYFRAME_INSERT.search(stripped)
                if kf_m:
                    current_shot["keyframe_inserts"].append(
                        {"kind": kf_m.group(1).strip(), "text": kf_m.group(2).strip()}
                    )
                    continue
                if stripped and not stripped.startswith("---"):
                    current_shot["narration"].append(stripped)

    # 场景头声明数与实际块数不符 → warning（如第 2 集声明 3 个场景只有 1 个 Scene 块）
    if ep["scene_count_declared"] is not None and ep["scene_count_declared"] != len(ep["scenes"]):
        ep["warnings"].append(
            f"头部声明场景数 {ep['scene_count_declared']} 与正文 Scene 块数 {len(ep['scenes'])} 不符"
        )
    return ep


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def _validate_episode(ep: dict, cards: dict, errors: list[str], warnings: list[str]) -> None:
    tag = f"E{ep['episode']:02d}"
    shots = ep["shots"]

    if not shots:
        errors.append(f"{tag}: 未解析到任何镜头块")
        return

    prev_end: Optional[float] = None
    for sh in shots:
        loc = f"{tag}-S{sh['s_no']:02d}"
        if sh["start"] is None or sh["end"] is None:
            errors.append(f"{loc}: 时间码解析失败: {sh['tc_raw']}")
            continue
        if sh["end"] <= sh["start"]:
            errors.append(f"{loc}: 时间码区间非法 end<=start ({sh['tc_raw']})")
        if prev_end is not None and abs(sh["start"] - prev_end) > 1e-6:
            gap = sh["start"] - prev_end
            kind = "重叠" if gap < 0 else "间隙"
            errors.append(f"{loc}: 时间码不连续，与上一镜{kind} {abs(gap):.1f}s")
        prev_end = sh["end"]

        if not sh["shot_size"]:
            warnings.append(f"{loc}: 缺镜头行 [景别|运镜|简述]")
        if not sh["annotation"]:
            warnings.append(f"{loc}: 缺标注行 [Emotion|Hook|Connection|Sound]")

    total = shots[-1]["end"] - shots[0]["start"]
    if ep["duration_declared_s"] is not None:
        target = ep["duration_declared_s"]
        tol = max(5.0, target * 0.1)
        if abs(total - target) > tol:
            warnings.append(
                f"{tag}: 集总时长 {total:.1f}s 与头部声明约 {target}s 偏差超过 {tol:.0f}s"
            )

    # 场景引用可解析：SC-xx 变体（如 SC-01b）回退到基卡并记 warning；未知 → error
    sc_refs = {sh["scene"] for sh in shots if sh["scene"]}
    pr_refs: set[str] = set()
    for sh in shots:
        ann = sh.get("annotation", {})
        sc_refs.update(RE_SC_REF.findall(str(ann.get("Connection", ""))))
        pr_refs.update(RE_PR_REF.findall(
            " ".join([sh["title"], sh["brief"], " ".join(sh["narration"])]
                     + [k["text"] for k in sh["keyframe_inserts"]])))
    for ref in sorted(sc_refs):
        if ref in cards["by_id"]:
            continue
        base = re.match(r"SC-\d+", ref).group(0)
        if base in cards["by_id"]:
            warnings.append(f"{tag}: 场景引用 {ref} 无独立卡，回退基卡 {base}")
        else:
            errors.append(f"{tag}: 场景引用 {ref} 在场景卡中不存在")

    # 道具引用可解析：未知编号 → error（道具是硬引用，打错号会烧错钱）
    for ref in sorted(pr_refs):
        if ref not in cards["by_id"]:
            errors.append(f"{tag}: 道具引用 {ref} 在道具卡中不存在")

    # 台词说话人与资产卡对得上：卡上没有的角色按 NPC 记 warning（不判 error）
    for sh in shots:
        for d in sh["dialogues"]:
            if d["speaker"] not in cards["name_to_char"]:
                warnings.append(
                    f"{tag}-S{sh['s_no']:02d}: 说话人「{d['speaker']}」不在角色卡（按 NPC 处理）"
                )


# ---------------------------------------------------------------------------
# 汇总入账行
# ---------------------------------------------------------------------------
def _fingerprint(*parts: Any) -> str:
    joined = "|".join(str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def build_asset_rows(episodes: list[dict], cards: dict) -> tuple[list[dict], dict]:
    """生成卡资产行 + SB 行（全局 SB-001..N），附引用解析明细。"""
    rows: list[dict] = []
    stats: dict[str, Any] = {"npc_speakers": {}, "variant_scenes": {}, "char_refs": 0}

    for c in cards["cards"]:
        rows.append({
            "asset_id": c["id"],
            "type": c["type"],
            "description": c["name"],
            "source": "script",
            "status": "draft",
            "fingerprint": _fingerprint("card", c["type"], c["id"], c["name"]),
            "note": "来自资产卡",
        })

    global_no = 0
    for ep in episodes:
        tag = f"E{ep['episode']:02d}"
        for sh in ep["shots"]:
            global_no += 1
            loc = f"{tag}-S{sh['s_no']:02d}"

            scene_refs = {sh["scene"]} if sh["scene"] else set()
            scene_refs.update(RE_SC_REF.findall(str(sh["annotation"].get("Connection", ""))))
            # 变体引用（SC-01b）无独立卡时回退基卡，保证 parent_ids 在账本内可解析
            resolved_scenes = set()
            for ref in scene_refs:
                if not ref:
                    continue
                if ref in cards["by_id"]:
                    resolved_scenes.add(ref)
                    continue
                base = re.match(r"SC-\d+", ref).group(0)
                if base in cards["by_id"]:
                    resolved_scenes.add(base)
            scene_refs = resolved_scenes

            block_text = " ".join(
                [sh["title"], sh["brief"], " ".join(sh["narration"])]
                + [k["text"] for k in sh["keyframe_inserts"]]
                + [str(v) for v in sh["annotation"].values()]
            )
            prop_refs = set(RE_PR_REF.findall(block_text))
            char_ids = set()
            for name, cid in cards["name_to_char"].items():
                if name in block_text or any(d["speaker"] == name for d in sh["dialogues"]):
                    char_ids.add(cid)
            for d in sh["dialogues"]:
                if d["speaker"] not in cards["name_to_char"]:
                    stats["npc_speakers"].setdefault(d["speaker"], []).append(loc)

            parents = sorted(scene_refs | char_ids | prop_refs)
            duration = round(sh["end"] - sh["start"], 1)
            rows.append({
                "asset_id": f"SB-{global_no:03d}",
                "type": "storyboard",
                "parent_ids": ";".join(parents),
                "description": f"{loc} {sh['title']}",
                "source": "script",
                "status": "draft",
                "fingerprint": _fingerprint(
                    ep["episode"], sh["s_no"], sh["tc_raw"], sh["title"],
                    sh["shot_size"], sh["camera_move"], sh["brief"],
                    " ".join(sh["narration"]),
                    json.dumps(sh["dialogues"], ensure_ascii=False, sort_keys=True),
                    json.dumps(sh["annotation"], ensure_ascii=False, sort_keys=True),
                ),
                "cost": "0",
                "note": "批量入账 draft（账本先行）",
                "data": {
                    "episode": ep["episode"],
                    "ep_title": ep["title"],
                    "s_no": sh["s_no"],
                    "scene_qualifier": sh.get("scene_qualifier", ""),
                    "tc_start": sh["start"],
                    "tc_end": sh["end"],
                    "duration_s": duration,
                    "shot_size": sh["shot_size"],
                    "camera_move": sh["camera_move"],
                    "brief": sh["brief"],
                    "emotion": sh["annotation"].get("Emotion", ""),
                    "hook": sh["annotation"].get("Hook", ""),
                    "connection": sh["annotation"].get("Connection", ""),
                    "sound": sh["annotation"].get("Sound", ""),
                    "dialogues": sh["dialogues"],
                    "keyframe_inserts": sh["keyframe_inserts"],
                    "scene_refs": sorted(scene_refs),
                    "char_refs": sorted(char_ids),
                    "prop_refs": sorted(prop_refs),
                },
            })
            stats["char_refs"] += len(char_ids)

    return rows, stats


def import_storyboard(script_dir: str, db_path: str, report_path: Optional[str] = None,
                      dry_run: bool = False, actor: str = "import_storyboard") -> dict:
    """主入口：解析 → 校验 → 入账 → 报告。返回报告 dict。"""
    files = sorted(
        (f for f in os.listdir(script_dir) if RE_SHOT_FILENAME.match(f)),
        key=lambda f: int(RE_SHOT_FILENAME.match(f).group(1)),
    )
    cards = load_card_dictionary(script_dir)
    episodes = [parse_episode(os.path.join(script_dir, f)) for f in files]

    errors: list[str] = []
    warnings: list[str] = []
    for ep in episodes:
        _validate_episode(ep, cards, errors, warnings)
        warnings.extend(f"E{ep['episode']:02d}: {w}" if not w.startswith("E") else w
                        for w in ep["warnings"])

    rows, stats = build_asset_rows(episodes, cards)
    sb_rows = [r for r in rows if r["type"] == "storyboard"]

    report = {
        "schema_version": SCHEMA_VERSION,
        "script_dir": script_dir,
        "db_path": db_path,
        "expected": {"episodes": EXPECTED_EPISODES, "shots": EXPECTED_SHOTS},
        "parsed": {
            "episodes": len(episodes),
            "shots_total": len(sb_rows),
            "shots_per_episode": {f"E{e['episode']:02d}": len(e["shots"]) for e in episodes},
            "cards": {t: sum(1 for c in cards["cards"] if c["type"] == t)
                      for t in ("character", "scene", "prop")},
            "rows_total": len(rows),
            "char_ref_count": stats["char_refs"],
            "npc_speakers": {k: len(v) for k, v in sorted(stats["npc_speakers"].items())},
        },
        "delta_vs_expected": {
            "episodes": len(episodes) - EXPECTED_EPISODES,
            "shots": len(sb_rows) - EXPECTED_SHOTS,
        },
        "errors": errors,
        "warnings": warnings,
        "dry_run": dry_run,
    }

    if not dry_run:
        ledger = Ledger(db_path)
        try:
            with ledger.transaction():
                for r in rows:
                    payload = {k: v for k, v in r.items() if k != "asset_id"}
                    ledger.update(r["asset_id"], actor=actor, **payload)
            report["counts_after"] = ledger.counts()
        finally:
            ledger.close()

    if report_path:
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="分镜 md → 账本 draft（确定性 parser）")
    ap.add_argument("--script-dir", default=DEFAULT_SCRIPT_DIR)
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    ap.add_argument("--report", default=DEFAULT_REPORT_PATH)
    ap.add_argument("--dry-run", action="store_true", help="只解析校验不入账")
    args = ap.parse_args()

    report = import_storyboard(args.script_dir, args.db, args.report, args.dry_run)
    p, d = report["parsed"], report["delta_vs_expected"]
    print(f"集数: {p['episodes']} (预期 {report['expected']['episodes']}, 差 {d['episodes']:+d})")
    print(f"镜头: {p['shots_total']} (预期 {report['expected']['shots']}, 差 {d['shots']:+d})")
    for ep_no, n in p["shots_per_episode"].items():
        print(f"  {ep_no}: {n} 镜")
    print(f"资产卡: {p['cards']}")
    print(f"角色引用次数: {p['char_ref_count']} | NPC 说话人: {p['npc_speakers'] or '无'}")
    print(f"errors: {len(report['errors'])} | warnings: {len(report['warnings'])}")
    for e in report["errors"]:
        print(f"  [ERROR] {e}")
    for w in report["warnings"][:30]:
        print(f"  [WARN] {w}")
    if len(report["warnings"]) > 30:
        print(f"  ... 共 {len(report['warnings'])} 条 warning（完整清单见报告 JSON）")
    if not args.dry_run:
        print(f"入账后统计: {json.dumps(report.get('counts_after', {}), ensure_ascii=False)}")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
