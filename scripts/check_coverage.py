#!/usr/bin/env python3
"""
check_coverage.py —— 检索覆盖率自检（把"完整性不可保证"变成量化缺口表）
─────────────────────────────────────────────
用法：python3 scripts/check_coverage.py <research_dir>

读 03_raw_cases.json（含 _query 命中词）与 04_screened_cases.json（含 _track），
可选读 <research_dir>/名录.json（典型案例名录，格式 [{"名称":…,"案号":…,"来源":…}]，
由集团诉讼工作流 §3.2 联网核对后落盘），输出：

  <research_dir>/07_coverage.json   分节量化结果
  stdout                            检查点 2 / 报告附录可直接引用的摘要

分节结构：
  关键词覆盖 / 案件等级分布 / 法院层级分布 / 年份跨度 /
  时效结构(近三年占比+逾三年清单+指导案例豁免；实案模式另出"顺位内"子集) /
  track分布 / 名录核对（命中+缺口）/ 去重审计 / 顺位覆盖（实案模式·有 顺位法院.json 时填充）

法律依据：《最高人民法院关于统一法律适用加强类案检索的指导意见（试行）》
（法发〔2020〕24号）第八条要求检索说明含"检索主体、时间、平台、方法、结果"——
本自检即其中"方法/结果"的量化底稿。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
from pkulaw_utils import (  # noqa: E402
    load, normalize_caseno, derive_court_level, flatten_court, derive_year,
)


def flatten_04(data):
    """04 兼容数组或 {track: 数组} 两种顶层。"""
    if isinstance(data, dict):
        out = []
        for k, v in data.items():
            if isinstance(v, list):
                for r in v:
                    if isinstance(r, dict):
                        r = dict(r)
                        r.setdefault("_track", k)
                        out.append(r)
        return out
    return data if isinstance(data, list) else []


def _parse_date(s: str):
    """解析裁判日期到 (date, 是否精确到月日)。支持 YYYY.MM.DD / YYYY-MM-DD / YYYY；
    仅有年份时按该年 7-1 估（年中近似），精确标记为 False；无法识别返回 (None, False)。"""
    if not s:
        return None, False
    t = re.sub(r"[/-]", ".", str(s).strip())
    m = re.match(r"(\d{4})(?:\.(\d{1,2}))?(?:\.(\d{1,2}))?", t)
    if not m:
        return None, False
    y = int(m.group(1))
    if m.group(2) and m.group(3):
        try:
            return date(y, int(m.group(2)), int(m.group(3))), True
        except ValueError:
            pass
    return date(y, 7, 1), False


def _is_guiding(c: dict) -> bool:
    """识别指导性案例（第四条收尾句明文豁免时效优先级——应当参照，不因年代久而降级）。
    仅指最高法『指导性案例』；公报案例/典型案例不在豁免之列。"""
    blob = f"{c.get('CaseClassName') or ''} {c.get('DocumentAttr') or ''} {c.get('Title') or ''}"
    return "指导性案例" in blob or "指导案例" in blob


def _in_tiers(c: dict, tier_courts: set) -> bool:
    """案件审理法院是否命中四顺位目标法院（与顺位覆盖一致的宽松包含匹配）。"""
    _, court = flatten_court(c.get("LastInstanceCourt"))
    return any(tc in court or court in tc for tc in tier_courts if tc)


def _recency(raw: list, anchor: date) -> dict:
    """时效结构：近三年（基准日上溯三年）占比 + 逾三年清单。
    法发〔2020〕24号第四条收尾句『除指导性案例外，优先检索近三年的案例或者案件』的数据背书。
    口径：基准日=生成日(≈检索日)；**指导性案例豁免**（单列、不计入升降）；逾三年非不可用——
    深挖位优先近三年、逾三年作次级参考并标注。"""
    try:
        cutoff = anchor.replace(year=anchor.year - 3)
    except ValueError:            # 2-29 等边界
        cutoff = anchor.replace(year=anchor.year - 3, day=28)
    recent, old, unknown, guiding = 0, [], 0, 0
    for c in raw:
        if _is_guiding(c):        # 指导性案例：不受近三年优先约束，单列
            guiding += 1
            continue
        d, precise = _parse_date(c.get("LastInstanceDate") or "")
        if d is None:
            unknown += 1
        elif d >= cutoff:
            recent += 1
        else:
            disp = (c.get("LastInstanceDate") or "").strip()
            old.append({
                "案号": (c.get("CaseFlag") or "").strip() or (c.get("Title") or "")[:30],
                "审结": disp + ("" if precise else "（仅年份估）"),
                "标题": (c.get("Title") or "")[:36],
            })
    old.sort(key=lambda x: x["审结"])
    dated = recent + len(old)
    pct = (lambda n: f"{round(100 * n / dated)}%" if dated else "—")
    LIST_CAP = 80
    return {
        "基准日": anchor.isoformat(),
        "近三年界": cutoff.isoformat(),
        "近三年": {"件数": recent, "占比": pct(recent)},
        "逾三年": {"件数": len(old), "占比": pct(len(old)),
                "清单": old[:LIST_CAP],
                "清单截断": (f"另有 {len(old) - LIST_CAP} 件未列全" if len(old) > LIST_CAP else None)},
        "未知日期": unknown,
        "指导案例豁免": guiding,
        "说明": ("近三年=基准日上溯三年；占比按非指导性案例中有日期者计算。"
               "指导性案例依第四条收尾句豁免时效优先级（应当参照，单列不计入升降）。"
               "逾三年案件非不可用——「类案规则」章深挖位优先近三年、逾三年作次级参考并标注；"
               "「裁判规则全景」章仍可保留旧规案作新旧规对照（第四条收尾句仅约束深挖优先级）。"),
    }


def main():
    if len(sys.argv) < 2:
        sys.exit("用法：python3 scripts/check_coverage.py <research_dir>")
    rd = Path(sys.argv[1])
    raw = load(rd / "03_raw_cases.json")
    if not raw:
        sys.exit(f"未找到或为空：{rd/'03_raw_cases.json'}")
    screened = flatten_04(load(rd / "04_screened_cases.json"))

    # ── 关键词覆盖：每词命中数 + 独有命中（仅该词命中的案件）──
    word_hits, word_only = Counter(), Counter()
    for c in raw:
        qs = c.get("_query") or []
        for w in qs:
            word_hits[w] += 1
        if len(qs) == 1:
            word_only[qs[0]] += 1
    关键词覆盖 = {w: {"命中": n, "独有命中": word_only.get(w, 0)}
              for w, n in word_hits.most_common()}

    # ── 等级 / 层级 / 年份 ──
    grade = Counter()
    level = Counter()
    years = []
    for c in raw:
        cg = c.get("CaseGrade")
        if isinstance(cg, dict) and cg:  # 旧版 MCP：嵌套 dict
            grade["普通(07)" if any("07" in k for k in cg) else
                   "/".join(sorted(set(cg.values())))] += 1
        elif isinstance(cg, list) and cg:  # 新版 MCP：list（如 ["普通案例"]）
            is_pu = any(("07" in str(x)) or ("普通案例" in str(x)) for x in cg)
            grade["普通案例(07)" if is_pu else "/".join(str(x) for x in cg)] += 1
        else:
            grade["（无等级）"] += 1
        _, court = flatten_court(c.get("LastInstanceCourt"))
        level[derive_court_level(court)] += 1
        y = derive_year(c.get("LastInstanceDate") or "")
        if y:
            years.append(y)
    年份跨度 = ({"最早": min(years), "最晚": max(years),
              "分布": dict(sorted(Counter(years).items()))} if years else {})

    # ── 顺位法院（实案模式）：检查点1确认后落盘，供顺位覆盖与顺位内时效共用 ──
    tiers = None
    tiers_path = rd / "顺位法院.json"
    if tiers_path.exists():
        tiers = json.loads(tiers_path.read_text(encoding="utf-8"))

    # ── 时效结构：近三年优先的数据背书（第四条收尾句）──
    时效结构 = _recency(raw, date.today())
    if tiers:
        # 顺位内子集=命中四顺位目标法院者，是「类案规则」章深挖的候选总体；
        # 近三年优先在此子集内执行（全景章用全池，不受此约束）。
        tier_courts = {tc for cs in tiers.values() if isinstance(cs, list) for tc in cs}
        顺位内 = [c for c in raw if _in_tiers(c, tier_courts)]
        sub = _recency(顺位内, date.today())
        时效结构["顺位内"] = {
            "样本": len(顺位内),
            "近三年": sub["近三年"], "逾三年": sub["逾三年"],
            "未知日期": sub["未知日期"], "指导案例豁免": sub["指导案例豁免"],
            "说明": ("顺位内=命中四顺位目标法院的子集，是「类案规则」章深挖的候选总体；"
                   "近三年优先在此子集内执行，逾三年清单即该章深挖位的降级候选。"),
        }

    # ── track 分布（有 04 时）──
    track分布 = dict(Counter(r.get("_track") or "（未标）" for r in screened)) if screened else {}

    # ── 名录核对：典型案例名录 vs 样本池 ──
    名录核对 = None
    roster_path = rd / "名录.json"
    if roster_path.exists():
        roster = json.loads(roster_path.read_text(encoding="utf-8"))
        pool_nos = {normalize_caseno(c.get("CaseFlag") or "") for c in raw} - {""}
        pool_titles = [c.get("Title") or "" for c in raw]
        hits, gaps = [], []
        for item in roster:
            no = normalize_caseno(item.get("案号") or "")
            name = (item.get("名称") or "").strip()
            if no and no in pool_nos:
                hits.append({**item, "匹配方式": "案号"})
            elif name and any(name[:12] in t for t in pool_titles):
                hits.append({**item, "匹配方式": "名称（粗匹配，请人工核对）"})
            else:
                gaps.append(item)
        名录核对 = {"名录数": len(roster), "命中数": len(hits),
                "命中": hits, "缺口": gaps}

    # ── 顺位覆盖（实案模式）：复用上方已加载的 tiers ──
    顺位覆盖 = None
    if tiers:
        顺位覆盖 = {}
        gc_index = Path(__file__).resolve().parent.parent / "data" / "guiding_cases" / "index.json"
        顺位覆盖["顺位1_指导性案例"] = {
            "本地数据集": (f"{len(json.loads(gc_index.read_text(encoding='utf-8')))} 案可查"
                       if gc_index.exists() else "未建（运行 scripts/fetch_guiding_cases.py）"),
            "说明": "主题相关的指导性案例由 AI 检索数据集后列示，引用前按第九条核查效力",
        }
        for tier, courts in tiers.items():
            if not isinstance(courts, list):
                continue
            hits = []
            for c in raw:
                _, court = flatten_court(c.get("LastInstanceCourt"))
                if any(tc in court or court in tc for tc in courts if tc):
                    hits.append(c.get("CaseFlag") or c.get("Title", ""))
            顺位覆盖[tier] = {"目标法院": courts, "命中": len(hits),
                          "案件": hits[:50],
                          "缺口提示": "" if hits else "本顺位 0 命中——补检或在检查点2如实呈报"}

    # ── 去重审计 ──
    gid_cnt = Counter(c.get("Gid") for c in raw if c.get("Gid"))
    dup_gids = [g for g, n in gid_cnt.items() if n > 1]
    flag2gids = {}
    for c in raw:
        f = normalize_caseno(c.get("CaseFlag") or "")
        if f:
            flag2gids.setdefault(f, set()).add(c.get("Gid"))
    same_flag = {f: sorted(g for g in gs if g) for f, gs in flag2gids.items() if len(gs) > 1}

    coverage = {
        "生成日期": date.today().isoformat(),
        "样本池": {"03条数": len(raw), "04条数": len(screened) or None},
        "关键词覆盖": 关键词覆盖,
        "案件等级分布": dict(grade),
        "法院层级分布": dict(level.most_common()),
        "年份跨度": 年份跨度,
        "时效结构": 时效结构,
        "track分布": track分布,
        "名录核对": 名录核对,
        "去重审计": {"Gid重复": dup_gids, "同案号多Gid": same_flag},
        "顺位覆盖": 顺位覆盖,  # 实案模式：检查点1确认后落盘 顺位法院.json 即填充
    }
    out = rd / "07_coverage.json"
    out.write_text(json.dumps(coverage, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 摘要（检查点 2 / 报告附录"检索过程说明"可直接引用）──
    print(f"=== 检索覆盖率自检（写入 {out.name}）===\n")
    print(f"样本池：03 共 {len(raw)} 条" + (f"，04 共 {len(screened)} 条" if screened else ""))
    print(f"案件等级：{dict(grade)}")
    print(f"法院层级：{dict(level.most_common())}")
    if 年份跨度:
        print(f"年份跨度：{年份跨度['最早']}–{年份跨度['最晚']}")
    rc = 时效结构
    print(f"时效结构（基准日 {rc['基准日']}，近三年界 {rc['近三年界']}）："
          f"近三年 {rc['近三年']['件数']} 件（{rc['近三年']['占比']}）、"
          f"逾三年 {rc['逾三年']['件数']} 件（{rc['逾三年']['占比']}）"
          + (f"、未知日期 {rc['未知日期']} 件" if rc['未知日期'] else "")
          + (f"、指导案例豁免 {rc['指导案例豁免']} 件" if rc['指导案例豁免'] else ""))
    if rc["逾三年"]["清单"]:
        print("  ▸ 逾三年案件（深挖位作次级参考并标注；旧规案可留作全景章对照）：")
        for it in rc["逾三年"]["清单"][:8]:
            print(f"     · {it['案号']}（{it['审结']}）{it['标题']}")
        if rc["逾三年"].get("清单截断"):
            print(f"     … {rc['逾三年']['清单截断']}")
    if rc.get("顺位内"):
        sub = rc["顺位内"]
        print(f"  ▸ 顺位内子集（类案规则章深挖候选，命中四顺位法院 {sub['样本']} 件）："
              f"近三年 {sub['近三年']['件数']} 件（{sub['近三年']['占比']}）、"
              f"逾三年 {sub['逾三年']['件数']} 件（{sub['逾三年']['占比']}）"
              + (f"、指导案例豁免 {sub['指导案例豁免']} 件" if sub['指导案例豁免'] else ""))
    print("\n关键词命中（按命中数降序，独有命中=仅该词召回）：")
    for w, st in 关键词覆盖.items():
        print(f"  {w}: 命中 {st['命中']}，独有 {st['独有命中']}")
    zero_only = [w for w, st in 关键词覆盖.items() if st["独有命中"] == 0]
    if zero_only:
        print(f"  ▸ 独有命中为 0 的词（边际贡献存疑，可考虑换词）：{zero_only}")
    if 名录核对:
        print(f"\n名录核对：{名录核对['命中数']}/{名录核对['名录数']} 命中")
        for g in 名录核对["缺口"]:
            print(f"  ✗ 缺口：{g.get('名称','?')}（{g.get('案号','无案号')}，{g.get('来源','')}）→ 建议补检")
    else:
        print("\n名录核对：未提供 名录.json，跳过（集团诉讼工作流 §3.2 联网核对后落盘即可启用）")
    if dup_gids or same_flag:
        print(f"\n⚠️ 去重审计异常：Gid重复 {dup_gids or '无'}；同案号多Gid {same_flag or '无'}")
    else:
        print("\n去重审计：通过（无 Gid 重复、无同案号多 Gid）")
    if 顺位覆盖:
        print("\n顺位覆盖（法发〔2020〕24号第四条·实案模式）：")
        for tier, info in 顺位覆盖.items():
            if "命中" in info:
                gap = f"　⚠️ {info['缺口提示']}" if info.get("缺口提示") else ""
                print(f"  {tier}: {info['命中']} 件（目标法院 {info['目标法院']}）{gap}")
            else:
                print(f"  {tier}: {info.get('本地数据集', '')}")
    print("\n▸ 本结果可作为报告附录「检索过程说明」的方法/结果底稿"
          "（法发〔2020〕24号第八条要素）。缺口须在检查点 2 如实呈报。")


if __name__ == "__main__":
    main()
