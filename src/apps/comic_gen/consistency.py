"""跨镜头一致性 — Seedance r2v 锁定行提示词 + @图片N 别名生成器。

来源: T-V2 调研 §3.1 #2（侠客漫 seedance-final-video-prompt 的锁定行/别名思想,
license=Other —— 只重写实现, 不 vendor 代码）。纯函数, 无 I/O。

五分型锁定行（每张参考图一条, 告诉 r2v 模型"这张图锁什么、不锁什么"）:
- character_identity 角色身份: 全程 100% 锁定五官/脸型/发型/年龄感/体态,
  禁止换脸换发型变年龄 —— 跨镜头同脸的核心
- character_outfit 服装变装: 只锁服装体态, 脸以身份参考图为准（换装不破相）
- faceless_reference 无脸参考: 禁止读取五官/肖像（背影/剪影/道具图防污染）
- scene 场景: 锁 space/lighting/color/camera-axis（空间/光照/色彩/轴线）
- group 群像: 不要求个体同脸（人群/背景路人）

@图片N 别名: build_reference_aliases 把资产名映射成 Seedance 可寻址的
@图片1..N（按传入顺序）。别名表随 task 持久化便于审计（上游职责）。

接入点: prompt_assembly.assemble_r2v_prompt_with_locks（r2v 提交前把锁定行
拼进 prompt）。reference 分型元数据到齐后, pipeline 只需一行调用。

T-B6 新增: check_presence_and_continuity —— 在场性/跨镜连续性机器 lint
（纯数据进出, 无 I/O, 方便单测与后续接 runner / 账本数据）。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Optional, Tuple


class ReferenceLockKind(str, Enum):
    CHARACTER_IDENTITY = "character_identity"
    CHARACTER_OUTFIT = "character_outfit"
    FACELESS = "faceless_reference"
    SCENE = "scene"
    GROUP = "group"


@dataclass(frozen=True)
class ReferenceLock:
    """一张参考图 + 它的分型。description 为可选英文锁定描述（禁人名）。"""
    name: str
    kind: ReferenceLockKind
    description: str = ""
    alias: str = field(default="")  # 由 build_reference_lock_lines 自动填充


# 五分型锁定行模板（重写实现; 锁定语义对齐 T-V2 §2.2/§3.1#2 的口径）
LOCK_LINE_TEMPLATES: Dict[ReferenceLockKind, str] = {
    ReferenceLockKind.CHARACTER_IDENTITY: (
        "{alias}（{name}｜角色身份参考）全程 100% 锁定该角色：五官、脸型、发型、"
        "年龄感与体态一律以本图为准，任何镜头不得换脸、换发型或变年龄。"
    ),
    ReferenceLockKind.CHARACTER_OUTFIT: (
        "{alias}（{name}｜服装变装参考）仅锁定本图中的服装与体态；"
        "脸部仍以该角色的身份参考图为准。"
    ),
    ReferenceLockKind.FACELESS: (
        "{alias}（{name}｜无脸参考）仅作构图、氛围与物件参考；"
        "禁止读取本图中的脸、五官与肖像信息。"
    ),
    ReferenceLockKind.SCENE: (
        "{alias}（{name}｜场景参考）锁定空间结构、光照、色彩与镜头轴线；"
        "镜头运动保持同一轴线与透视，不得改变场景布局。"
    ),
    ReferenceLockKind.GROUP: (
        "{alias}（{name}｜群像参考）作为整体构图与密度参考；"
        "不要求画面中每个个体与参考图同脸。"
    ),
}


def build_reference_aliases(names: Iterable[str]) -> Dict[str, str]:
    """资产名 → @图片N 别名（N 从 1 起, 按传入顺序）。

    同名资产重复出现时后者覆盖前者（同一资产两张图应去重后再传入）。
    """
    return {name: f"@图片{idx}" for idx, name in enumerate(names, start=1)}


def build_reference_lock_line(ref: ReferenceLock, alias: str) -> str:
    """单条锁定行 = 分型模板 + 可选外观锁定描述。"""
    template = LOCK_LINE_TEMPLATES[ref.kind]
    line = template.format(alias=alias, name=ref.name)
    if ref.description:
        line += f"外观锁定：{ref.description}"
    return line


def build_reference_lock_lines(refs: List[ReferenceLock]) -> List[str]:
    """每个参考一条锁定行, alias 按 refs 顺序自动分配为 @图片1..N。"""
    lines: List[str] = []
    for idx, ref in enumerate(refs, start=1):
        alias = f"@图片{idx}"
        lines.append(build_reference_lock_line(ref, alias))
    return lines


def assemble_r2v_prompt(
    base_prompt: str,
    refs: List[ReferenceLock],
) -> Tuple[str, Dict[str, str]]:
    """R2V 提交前的最终 prompt：基础画面描述 + 锁定行块。

    返回 (prompt, alias_map)。alias_map 为 资产名→@图片N（随 task 持久化便于审计）。
    没有参考时原样返回基础 prompt（空别名表）。
    """
    alias_map = build_reference_aliases([ref.name for ref in refs])
    if not refs:
        return base_prompt, alias_map
    lock_block = "参考图锁定：\n" + "\n".join(build_reference_lock_lines(refs))
    clean = (base_prompt or "").rstrip()
    prompt = f"{clean}\n\n{lock_block}" if clean else lock_block
    return prompt, alias_map


# ── T-B6 在场性 / 跨镜连续性 lint（纯数据进出, 无 I/O） ─────────────────────
# 方法论来源: zenstory-ai/drama-skills（MIT License,
# skills/short-drama-storyboard/references/review-and-fixtures.md）——
# "相邻镜头的站位、朝向、视线、持物、伤势、光态与可读文字连续"；
# "关键帧正文点名的人物、地点或道具必须出现在本镜视觉依据里"；
# 跨镜物件"不能让合法终点之间靠镜外瞬移衔接"。这里只把其中**机器可查**
# 的子集（在场声明核对 + 出现→消失→又出现）落成纯函数；站位/视线/持物
# 的语义连续性仍留给 LLM 注入清单（llm.CONTINUITY_CHECKLIST）与人工审查。


@dataclass(frozen=True)
class ShotRef:
    """一镜的引用图切片: 所属场景 + 在场实体引用 + 可选时间码窗口（秒）。

    时间码缺失（None）时退化为场内输入顺序相邻——账本 SB 行（data.tc_start/
    tc_end）与分镜 DSL（[mm:ss.d – mm:ss.d]）均可直连, 无时间码也能跑。
    """
    shot_id: str
    scene_id: str
    character_ids: Tuple[str, ...] = ()
    prop_ids: Tuple[str, ...] = ()
    start_s: Optional[float] = None
    end_s: Optional[float] = None


@dataclass(frozen=True)
class SceneCast:
    """场景声明的角色表（分镜之外的在场基准; 缺失则跳过声明类检查）。"""
    scene_id: str
    character_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ContinuityWarning:
    """一条 lint warning（warning 级, 永不拦截流程）。"""
    kind: str          # cast_member_never_on_screen | character_outside_cast | entity_gap
    scene_id: str
    entity_kind: str   # character | prop
    entity_id: str
    shot_ids: Tuple[str, ...]
    message: str


def check_presence_and_continuity(
    shots: List[ShotRef],
    scene_casts: Iterable[SceneCast] = (),
) -> List[ContinuityWarning]:
    """在场性 + 跨镜连续性 lint（纯函数, 输入输出全是数据）。

    三项检查（全部 warning 级）:
    1. cast_member_never_on_screen: 场景角色表声明了角色, 但该场景没有任何
       一镜让它到场（声明了却没落实——"不在场"必须如实记录, 不能只停在表里）。
    2. character_outside_cast: 镜头引用了角色表之外的角色（表缺失时跳过）。
    3. entity_gap: 同一场景内角色/道具"出现→消失→又出现", 中段消失的镜头
       即瞬移/蒸发嫌疑（道具跨镜连续性是重灾区: "四镜都出现的一支笔和一件
       外套是同一类问题"）。

    场内排序: 时间码全齐按 (start_s, end_s, 输入序), 否则按输入序。
    输出按 (scene_id, kind, entity_id) 排序, 保证同输入同输出（可快照测试）。
    """
    warnings: List[ContinuityWarning] = []

    by_scene: Dict[str, List[ShotRef]] = {}
    for shot in shots:
        by_scene.setdefault(shot.scene_id, []).append(shot)

    cast_map: Dict[str, Tuple[str, ...]] = {
        cast.scene_id: tuple(cast.character_ids) for cast in scene_casts
    }

    # 1) 声明了却从未在场
    for scene_id, declared in cast_map.items():
        on_screen: set = set()
        for shot in by_scene.get(scene_id, []):
            on_screen.update(shot.character_ids)
        for cid in declared:
            if cid not in on_screen:
                warnings.append(ContinuityWarning(
                    kind="cast_member_never_on_screen",
                    scene_id=scene_id,
                    entity_kind="character",
                    entity_id=cid,
                    shot_ids=(),
                    message=f"角色 {cid} 在场景 {scene_id} 角色表中，但该场景没有任何镜头让它到场",
                ))

    for scene_id, scene_shots in by_scene.items():
        indexed = list(enumerate(scene_shots))
        if all(s.start_s is not None and s.end_s is not None for s in scene_shots):
            ordered = [s for _, s in sorted(indexed, key=lambda p: (p[1].start_s, p[1].end_s, p[0]))]
        else:
            ordered = [s for _, s in sorted(indexed, key=lambda p: p[0])]

        # 2) 场外角色
        declared = cast_map.get(scene_id)
        if declared is not None:
            declared_set = set(declared)
            for shot in ordered:
                for cid in shot.character_ids:
                    if cid not in declared_set:
                        warnings.append(ContinuityWarning(
                            kind="character_outside_cast",
                            scene_id=scene_id,
                            entity_kind="character",
                            entity_id=cid,
                            shot_ids=(shot.shot_id,),
                            message=f"镜头 {shot.shot_id} 引用角色 {cid}，但其不在场景 {scene_id} 角色表",
                        ))

        # 3) 出现→消失→又出现
        for kind_name, attr in (("character", "character_ids"), ("prop", "prop_ids")):
            presence: Dict[str, List[int]] = {}
            for idx, shot in enumerate(ordered):
                for eid in getattr(shot, attr):
                    presence.setdefault(eid, []).append(idx)
            for eid, idxs in presence.items():
                if len(idxs) < 2:
                    continue
                idx_set = set(idxs)
                first, last = idxs[0], idxs[-1]
                missing = [ordered[i].shot_id for i in range(first + 1, last) if i not in idx_set]
                if missing:
                    noun = "角色" if kind_name == "character" else "道具"
                    warnings.append(ContinuityWarning(
                        kind="entity_gap",
                        scene_id=scene_id,
                        entity_kind=kind_name,
                        entity_id=eid,
                        shot_ids=tuple(missing),
                        message=(
                            f"{noun} {eid} 在场景 {scene_id} 中段消失于镜头 "
                            f"{'、'.join(missing)} 后又出现——疑似镜外瞬移/蒸发，"
                            f"需在分镜中交代离场与回归依据"
                        ),
                    ))

    warnings.sort(key=lambda w: (w.scene_id, w.kind, w.entity_id))
    return warnings
