"""
pipeline_schema.py —— 管道各阶段数据契约校验（宽松白名单）
─────────────────────────────────────────────
对 03/04/05/06 四个阶段的 JSON 做结构与类型校验，把"编码漏填/写错键名/类型错"
在产出 Excel/报告之前当场抓出来，而不是等到成品里出现空列才发现。

设计原则（为双模式等后续扩展零返工）：
  · **宽松白名单**：只校验"已知字段"的类型/枚举/一致性，**不禁止未知新字段**——
    后续追加 `_顺位`/`参照效力`/`相关度依据` 等新字段不会触发任何报错。
  · 区分 error（数据契约被破坏，必须修复）与 warning（可疑但不阻断）。
  · 零第三方依赖，纯标准库。

被 scripts/validate_pipeline.py（CLI）与两套 run_analytics（warn 模式）调用。
"""
from __future__ import annotations

import re

RESULT_ENUM = {"驳回", "全部支持", "部分支持", "撤销改判", ""}
RELEVANCE_ENUM = {"高", "中", "低", ""}
# core/parallel/typical 为证券集团案管道词；其余为通用管道分流词（学理/散案）。
TRACK_ENUM = {"core", "parallel", "typical",
              "分析民事", "对照行政", "下级留痕", "权威附录", "剔除"}


def _grade_has(cg, token: str) -> bool:
    """CaseGrade 既可能是旧版嵌套 dict，也可能是新版 MCP 的 list（如 ["普通案例"]）。"""
    if isinstance(cg, dict):
        return any(token in k for k in cg)
    if isinstance(cg, list):
        return any(token in str(x) for x in cg)
    return False


def _norm_name(s: str) -> str:
    """焦点/问题名归一化（去空白、统一全半角引号括号），用于发现拼写不一致。"""
    if not isinstance(s, str):
        return ""
    s = re.sub(r"\s+", "", s)
    for a, b in (("(", "（"), (")", "）"), ('"', "“"), ("'", "‘")):
        s = s.replace(a, b)
    return s


class Report:
    def __init__(self, stage: str):
        self.stage = stage
        self.errors: list = []
        self.warnings: list = []
        self.info: list = []

    def err(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def note(self, msg):
        self.info.append(msg)

    @property
    def ok(self):
        return not self.errors


# 汇编/典型案例/法院报道类条目的标题特征（这类条目无单独案号，属正常）
_COLLECTION_HINT = ("发布", "十大", "典型案例", "参考案例", "案例汇编", "白皮书",
                    "年度", "营商环境", "人民法院报", "审判工作", "纪要")


def _leaf(nested) -> str:
    """取多级嵌套对象最末级（键最长）的值；list 取首元素；非 dict/list 返回空。"""
    if isinstance(nested, list):
        return str(nested[0]) if nested else ""
    if not isinstance(nested, dict) or not nested:
        return ""
    return sorted(nested.items(), key=lambda kv: len(kv[0]))[-1][1]


def _is_collection_entry(r: dict) -> bool:
    """判断是否为'汇编/典型案例/报道'条目：无 07/普通案例、或非判决书裁定书、或标题含汇编特征。
    这类条目合法地没有单独案号，缺 CaseFlag 仅作 warning。"""
    cg = r.get("CaseGrade")
    has07 = _grade_has(cg, "07") or _grade_has(cg, "普通案例")
    doc = _leaf(r.get("DocumentAttr"))
    is_doc = doc in ("判决书", "裁定书", "调解书", "决定书")
    title = r.get("Title") or ""
    looks_collection = any(h in title for h in _COLLECTION_HINT)
    return (not has07) or (not is_doc) or looks_collection


def _check_record_core(r: dict, i: int, rep: Report, require_body_fields: bool):
    """单条记录的核心契约（03/04 core/typical 共用）。"""
    for key in ("Gid", "Title"):
        if not r.get(key):
            rep.err(f"第{i}条缺必填字段 {key}（Title={str(r.get('Title'))[:20]}…）")
    if not r.get("CaseFlag"):
        if _is_collection_entry(r):
            rep.warn(f"第{i}条无案号（疑似汇编/典型案例条目：{str(r.get('Title'))[:24]}…），"
                     f"如确为判决请补 CaseFlag")
        else:
            rep.err(f"第{i}条缺必填字段 CaseFlag（Title={str(r.get('Title'))[:20]}…）")
    cg = r.get("CaseGrade")
    if cg is not None and not isinstance(cg, (dict, list)):
        rep.err(f"第{i}条 CaseGrade 应为嵌套对象或数组（MCP 原样），实为 {type(cg).__name__}")
    lic = r.get("LastInstanceCourt")
    if lic is not None and not isinstance(lic, (dict, list)):
        rep.err(f"第{i}条 LastInstanceCourt 应为嵌套对象或数组，实为 {type(lic).__name__}")
    if not r.get("Url"):
        rep.warn(f"第{i}条（{r.get('CaseFlag','?')}）缺 Url，引用将无法溯源")
    # 双轨分流依据：普通案例(07)应有正文
    if require_body_fields and (_grade_has(cg, "07") or _grade_has(cg, "普通案例")):
        if not r.get("Ascertain"):
            rep.warn(f"第{i}条（{r.get('CaseFlag','?')}）CaseGrade=07 但 Ascertain 为空——"
                     f"请核实是否字段丢失（双轨分流依赖它）")


def validate_03(records) -> Report:
    rep = Report("03_raw_cases")
    if not isinstance(records, list):
        rep.err("03 顶层必须是数组")
        return rep
    seen = {}
    for i, r in enumerate(records, 1):
        if not isinstance(r, dict):
            rep.err(f"第{i}条不是对象")
            continue
        _check_record_core(r, i, rep, require_body_fields=True)
        g = r.get("Gid")
        if g:
            if g in seen:
                rep.err(f"Gid 重复：{g}（第{seen[g]}与第{i}条）——03 应已按 Gid 去重")
            seen[g] = i
    rep.note(f"共 {len(records)} 条，唯一 Gid {len(seen)} 个")
    return rep


def validate_04(data) -> Report:
    rep = Report("04_screened_cases")
    # 兼容两种顶层：数组（含 _track 标记）或 {core/parallel/typical: [...]}
    records = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, list):
                for r in v:
                    if isinstance(r, dict):
                        r = dict(r)
                        r.setdefault("_track", k)
                    records.append(r)
    elif isinstance(data, list):
        records = data
    else:
        rep.err("04 顶层必须是数组或 {track: 数组}")
        return rep
    for i, r in enumerate(records, 1):
        if not isinstance(r, dict):
            rep.err(f"第{i}条不是对象")
            continue
        track = r.get("_track")
        if track is not None and track not in TRACK_ENUM:
            rep.err(f"第{i}条 _track={track!r} 不在 {sorted(TRACK_ENUM)}")
        if track == "parallel":
            # 平行判决仅留痕：只要求案号+标题
            if not r.get("CaseFlag") or not r.get("Title"):
                rep.err(f"第{i}条平行判决留痕缺 CaseFlag/Title")
        else:
            _check_record_core(r, i, rep,
                               require_body_fields=(track in (None, "core", "分析民事")))
    rep.note(f"共 {len(records)} 条")
    return rep


def validate_05(records) -> Report:
    rep = Report("05_enriched_cases")
    if not isinstance(records, list):
        rep.err("05 顶层必须是数组")
        return rep
    name_variants: dict = {}   # 归一名 -> {原始名}
    coverage = {}              # 原始焦点/问题名 -> 出现案件数
    for i, r in enumerate(records, 1):
        if not isinstance(r, dict):
            rep.err(f"第{i}条不是对象")
            continue
        flag = r.get("CaseFlag") or r.get("案号") or f"第{i}条"
        if not r.get("Gid"):
            rep.err(f"{flag} 缺 Gid（字段保全铁律：原始字段不可丢）")
        rc = r.get("裁判结果分类")
        if rc is not None and rc not in RESULT_ENUM:
            rep.err(f"{flag} 裁判结果分类={rc!r} 不在枚举 {sorted(RESULT_ENUM - {''})}")
        amt = r.get("判赔金额")
        if amt is not None and not isinstance(amt, (int, float)):
            rep.err(f"{flag} 判赔金额应为数值或 null，实为 {type(amt).__name__}（{amt!r}）")
        rel = r.get("相关度")
        if rel is not None and rel not in RELEVANCE_ENUM:
            rep.err(f"{flag} 相关度={rel!r} 不在 {sorted(RELEVANCE_ENUM - {''})}")
        if "_norm" in r and not isinstance(r["_norm"], dict):
            rep.err(f"{flag} _norm 应为对象")

        # 通用工作流：焦点立场 + 抗辩
        fp = r.get("焦点立场")
        if fp is not None:
            if not isinstance(fp, dict):
                rep.err(f"{flag} 焦点立场应为对象 {{焦点名: {{立场, 理由}}}}")
            else:
                for focus, info in fp.items():
                    coverage[focus] = coverage.get(focus, 0) + 1
                    name_variants.setdefault(_norm_name(focus), set()).add(focus)
                    if not isinstance(info, dict) or "立场" not in info:
                        rep.err(f"{flag} 焦点「{focus}」缺 立场 键")
        defs = r.get("抗辩")
        if defs is not None:
            if not isinstance(defs, list):
                rep.err(f"{flag} 抗辩应为数组")
            else:
                for d in defs:
                    if not isinstance(d, dict) or "理由" not in d:
                        rep.err(f"{flag} 抗辩项缺 理由 键")
                    elif not isinstance(d.get("是否被采纳"), (bool, type(None))):
                        rep.err(f"{flag} 抗辩「{d.get('理由')}」是否被采纳 应为 true/false/null")

        # 集团诉讼工作流：问题观点
        qv = r.get("问题观点")
        if qv is not None:
            if not isinstance(qv, dict):
                rep.err(f"{flag} 问题观点应为对象 {{问题名: {{倾向标签, …}}}}")
            else:
                for q, info in qv.items():
                    coverage[q] = coverage.get(q, 0) + 1
                    name_variants.setdefault(_norm_name(q), set()).add(q)
                    if not isinstance(info, dict):
                        rep.err(f"{flag} 问题「{q}」的值应为对象")

    # 焦点/问题名拼写一致性：归一化后同名但原文不同 → 编码时拼写漂移，聚合会分裂
    for norm, variants in name_variants.items():
        if len(variants) > 1:
            rep.err(f"焦点/问题名拼写不一致（聚合将分裂为多列）：{sorted(variants)}")
    if coverage:
        rep.note("焦点/问题覆盖：" + "、".join(f"{k}×{v}" for k, v in
                                              sorted(coverage.items(), key=lambda x: -x[1])))
    rep.note(f"共 {len(records)} 条")
    return rep


def validate_06(analytics) -> Report:
    rep = Report("06_analytics")
    if not isinstance(analytics, dict):
        rep.err("06 顶层必须是对象")
        return rep
    if "样本量" not in analytics and "N" not in analytics:
        rep.err("缺 样本量/N")
    depth = analytics.get("深度档")
    if not isinstance(depth, dict) or "mode" not in depth:
        rep.err("缺 深度档.mode（样本自适应判定）")
    if "定量档" not in analytics:
        rep.err("缺 定量档")
    gate = analytics.get("地域分歧")
    if not isinstance(gate, dict) or "report" not in gate:
        rep.err("缺 地域分歧.report（分歧闸门判定）")
    return rep
