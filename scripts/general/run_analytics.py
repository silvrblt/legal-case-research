"""
run_analytics.py —— 六维统计分析（样本量自适应 + 出图）
─────────────────────────────────────────────
用法：python3 scripts/general/run_analytics.py <research_dir>
输入：<research_dir>/05_enriched_cases.json （已规范化 + 已编码）
输出：<research_dir>/06_analytics.json
      <research_dir>/output/_charts/*.png + manifest.json （供报告占位符 ![chart:key] 插图）

脚本计算"可计算"的统计并出图；法律解读由 Claude Code 基于本结果在报告中完成。
两条样本自适应规则（散案/集团案通用，见 scripts/common/stats_guard.py）：
  · 定量：样本越大解锁越强的统计（描述 → 卡方/Fisher → 建模）。
  · 定性：样本越小，单争点的论证越要深挖（depth_mode）。

每条 enriched case 约定包含（由 Claude Code 编码写入）：
  _norm: {法院层级, 地域, 裁判年份, 案由, 案件等级}
  编码字段: 裁判结果分类, 判赔金额, 维权开支, 相关度
  焦点立场: { "<焦点名>": {"立场": "支持原告|支持被告|未评述", "理由": "..."} }
  抗辩: [ {"理由": "技术中立", "是否被采纳": true/false}, ... ]
"""

import json
import sys
import statistics
import os
from collections import Counter, defaultdict
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)                       # scripts/
sys.path[:0] = [SCRIPTS, os.path.join(SCRIPTS, "common")]
import stats_guard as sg                              # 必需：样本量自适应闸门
import divergence as dv                               # 分歧检测（一等产出）
import pipeline_schema as ps                          # 数据契约校验（warn 模式）

# 图表层可选：matplotlib 缺失时优雅降级（分析仍写出，仅跳过出图）
try:
    import chart_theme as ct
    _HAS_CHART = True
except Exception as _e:                               # noqa: BLE001
    _HAS_CHART = False
    _CHART_ERR = str(_e)

RESULTS = ["全部支持", "部分支持", "驳回", "撤销改判"]


def crosstab(cases, row_key_fn, col_key_fn):
    """通用列联表：返回 {row: {col: count}}"""
    table = defaultdict(lambda: defaultdict(int))
    for c in cases:
        r = row_key_fn(c)
        col = col_key_fn(c)
        if r is None or r == "" or col is None or col == "":
            continue
        table[r][col] += 1
    return {r: dict(cols) for r, cols in table.items()}


def to_matrix(ct_dict):
    """把 {row:{col:n}} 转成 (rows, cols, matrix) 供 stats_guard.crosstab_test。"""
    rows = list(ct_dict.keys())
    cols = sorted({c for r in ct_dict.values() for c in r})
    matrix = [[ct_dict[r].get(c, 0) for c in cols] for r in rows]
    return rows, cols, matrix


def tested_crosstab(ct_dict):
    """列联表 + 自适应关联检验措辞，打包进同一对象供报告直接引用。"""
    rows, cols, matrix = to_matrix(ct_dict)
    test = sg.crosstab_test(matrix) if matrix and len(rows) >= 2 and len(cols) >= 2 else {
        "usable": False, "phrasing": "（样本不足，不作推断）"}
    return {"表": ct_dict, "检验": test}


def safe_norm(c, key):
    return (c.get("_norm") or {}).get(key, "")


def main():
    if len(sys.argv) < 2:
        sys.exit("用法：python3 scripts/general/run_analytics.py <research_dir>")
    research_dir = Path(sys.argv[1])
    path = research_dir / "05_enriched_cases.json"
    if not path.exists():
        sys.exit(f"未找到 {path}")
    cases = json.loads(path.read_text(encoding="utf-8"))
    n = len(cases)

    # 编码契约校验（warn 模式：只提示不阻断；严格校验用 scripts/validate_pipeline.py）
    rep = ps.validate_05(cases)
    for m in rep.errors:
        print(f"⚠️ [契约] {m}")

    analytics = {"样本量": n}

    # ── 样本量自适应（贯穿报告深度与定量强度的总闸门）──
    region_counts = dict(Counter(safe_norm(c, "地域") for c in cases if safe_norm(c, "地域")))
    level_counts = dict(Counter(safe_norm(c, "法院层级") for c in cases if safe_norm(c, "法院层级")))
    analytics["深度档"] = sg.depth_mode(n)              # qualitative_deep / quantitative_lead
    analytics["定量档"] = sg.stat_tier(n)              # T0_descriptive / T1_association / T2_model
    analytics["地域分歧"] = sg.divergence_gate(region_counts)   # report=False 时报告不得设地域分歧小节
    analytics["审级分歧"] = sg.divergence_gate(level_counts)

    # ── 描述性分布 ──
    analytics["分布"] = {
        "法院层级": dict(Counter(safe_norm(c, "法院层级") for c in cases)),
        "地域": dict(Counter(safe_norm(c, "地域") for c in cases)),
        "审级": dict(Counter(c.get("CaseClassName", "") for c in cases)),
        "裁判年份": dict(Counter(safe_norm(c, "裁判年份") for c in cases)),
        "案由": dict(Counter(safe_norm(c, "案由") for c in cases)),
        "裁判结果分类": dict(Counter(c.get("裁判结果分类", "") for c in cases)),
    }

    # ── 维度一：交叉关联（每张表自带自适应检验措辞）──
    analytics["维度一_交叉关联"] = {
        "法院层级×裁判结果": tested_crosstab(crosstab(
            cases, lambda c: safe_norm(c, "法院层级"), lambda c: c.get("裁判结果分类"))),
        "审级×裁判结果": tested_crosstab(crosstab(
            cases, lambda c: c.get("CaseClassName"), lambda c: c.get("裁判结果分类"))),
        "年份×裁判结果": tested_crosstab(crosstab(
            cases, lambda c: safe_norm(c, "裁判年份"), lambda c: c.get("裁判结果分类"))),
    }

    # ── 维度二：抗辩有效性 ──
    defense_stats = defaultdict(lambda: {"提出": 0, "采纳": 0})
    for c in cases:
        for d in c.get("抗辩", []):
            name = d.get("理由", "未命名")
            defense_stats[name]["提出"] += 1
            if d.get("是否被采纳"):
                defense_stats[name]["采纳"] += 1
    for name, s in defense_stats.items():
        s["成功率"] = round(s["采纳"] / s["提出"], 3) if s["提出"] else None
    analytics["维度二_抗辩有效性"] = dict(defense_stats)

    # ── 维度三：判赔金额 ──
    amounts = [c.get("判赔金额") for c in cases if isinstance(c.get("判赔金额"), (int, float))]
    if amounts:
        analytics["维度三_判赔金额"] = {
            "有金额案件数": len(amounts),
            "最低": min(amounts),
            "最高": max(amounts),
            "中位数": statistics.median(amounts),
            "平均": round(statistics.mean(amounts), 2),
        }
    else:
        analytics["维度三_判赔金额"] = {"说明": "无可解析的判赔金额数据"}

    # ── 维度四：演进拐点（按年份的裁判结果倾向序列）──
    year_result = defaultdict(lambda: Counter())
    for c in cases:
        y = safe_norm(c, "裁判年份")
        if y:
            year_result[y][c.get("裁判结果分类", "未知")] += 1
    analytics["维度四_年度趋势"] = {y: dict(cnt) for y, cnt in sorted(year_result.items())}

    # ── 维度五：各争议焦点的立场分布（分歧地图原料）+ 争点出现频次 ──
    focus_positions = defaultdict(lambda: Counter())
    focus_freq = Counter()
    for c in cases:
        for focus, info in (c.get("焦点立场") or {}).items():
            focus_freq[focus] += 1
            focus_positions[focus][info.get("立场", "未评述")] += 1
    analytics["维度五_焦点立场分布"] = {f: dict(cnt) for f, cnt in focus_positions.items()}
    analytics["争点出现频次"] = dict(focus_freq)

    # ── 分歧地图（一等产出）：同争点对立立场并存的争点清单 + 代表案对 ──
    analytics["分歧地图"] = dv.build_divergence(
        cases,
        get_positions=lambda c: {f: (i or {}).get("立场")
                                 for f, i in (c.get("焦点立场") or {}).items()},
        get_region=lambda c: safe_norm(c, "地域"),
        get_caseflag=lambda c: c.get("CaseFlag", ""),
    )

    # 维度六（要素-结果影响度）需法律解读，留给 Claude Code 基于上述结果撰写。
    analytics["维度六_说明"] = "要素-结果影响度排序由报告撰写时综合上述维度做法律解读得出。"

    out = research_dir / "06_analytics.json"
    out.write_text(json.dumps(analytics, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 出图（chart_theme；与证券版共用 manifest 键，报告用 ![chart:key] 插图）──
    chart_msg = "（未出图：matplotlib 不可用）" if not _HAS_CHART else _render_charts(
        research_dir, cases, analytics, n, focus_freq, year_result)

    print(f"六维统计完成，写入 {out}；样本 N={n}，深度档={analytics['深度档']['mode']}，"
          f"定量档={analytics['定量档']}，地域分歧={'报告' if analytics['地域分歧']['report'] else '不报告(样本不足)'}，"
          f"分歧争点 {len(analytics['分歧地图'])} 个。{chart_msg}")


def _render_charts(research_dir, cases, analytics, n, focus_freq, year_result):
    """生成三张统一主题图表并写 manifest.json；与证券版 manifest 键一致。"""
    from collections import OrderedDict
    charts_dir = research_dir / "output" / "_charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    ct.apply_theme()
    manifest = {}

    def od_desc(counter):
        return OrderedDict(Counter(counter).most_common())

    dist = analytics["分布"]
    # 图1：样本概览 2×2
    result_od = OrderedDict((k, dist["裁判结果分类"].get(k, 0)) for k in RESULTS
                            if dist["裁判结果分类"].get(k))
    fig = ct.overview_2x2([
        ("h", od_desc(dist["法院层级"]), "法院层级", None),
        ("h", od_desc(dist["地域"]), "地域分布", None),
        ("v", OrderedDict(sorted((y, v) for y, v in dist["裁判年份"].items() if y)), "裁判年份", None),
        ("h", result_od, "裁判结果", {"color": ct.NAVY}),
    ])
    ct.save_fig(fig, str(charts_dir / "overview.png"))
    manifest["overview"] = {"path": str(charts_dir / "overview.png"), "w": 540, "h": 372,
                            "caption": f"图 1　样本概览：法院层级、地域、年份与裁判结果分布（N={n}）"}

    # 图2：争点出现频次（最高项深红高亮）
    if focus_freq:
        freq_desc = OrderedDict(sorted(focus_freq.items(), key=lambda x: -x[1]))
        fig, ax = ct.single(figsize=(8.4, 5))
        ct.hbar_panel(ax, freq_desc, "各争议焦点在样本中的出现频次", highlight_max=True)
        ax.set_title("各争议焦点在样本中的出现频次", loc="left", fontsize=13,
                     fontweight="bold", color=ct.NAVY, pad=10)
        ct.save_fig(fig, str(charts_dir / "issue_freq.png"))
        manifest["issue_freq"] = {"path": str(charts_dir / "issue_freq.png"), "w": 540, "h": 321,
                                  "caption": "图 2　各争议焦点在样本中的出现频次"}

    # 图3：裁判结果年度演进（堆叠）
    years = sorted(y for y in year_result if y)
    if years:
        data = {r: [year_result[y].get(r, 0) for y in years] for r in RESULTS}
        fig, ax = ct.single(figsize=(8.4, 4.2))
        ct.stacked_year(ax, years, data, "裁判结果年度演进")
        ct.save_fig(fig, str(charts_dir / "result_year.png"))
        manifest["result_year"] = {"path": str(charts_dir / "result_year.png"), "w": 520, "h": 260,
                                   "caption": f"图 3　裁判结果年度演进（N={n}）"}

    # 图4：地域分布——基于 03 全量判决书（全国背景）。实案模式 05 仅为顺位子集，
    # 用 05 出地域图会退化成"上海市/金融法院"两条，无全国意义；故优先 03。
    import pkulaw_utils as pu
    region_cnt = Counter()
    raw3 = pu.load(research_dir / "03_raw_cases.json")
    if raw3:
        for r in raw3:
            if pu.flatten_leaf(r.get("DocumentAttr")) != "判决书":
                continue
            prov, court = pu.flatten_court(r.get("LastInstanceCourt"))
            prov = pu.normalize_province(prov, court)
            if prov:
                region_cnt[prov] += 1
        region_n, region_src = sum(region_cnt.values()), "全国样本判决书"
    else:
        for c in cases:
            prov = pu.normalize_province(safe_norm(c, "地域"), safe_norm(c, "法院全称"))
            if prov:
                region_cnt[prov] += 1
        region_n, region_src = sum(region_cnt.values()), "分析样本"
    if len(region_cnt) >= 2:
        region_od = OrderedDict(region_cnt.most_common())
        fig, ax = ct.single(figsize=(8.4, max(2.6, 0.42 * len(region_od) + 1.2)))
        ct.hbar_panel(ax, region_od, "", highlight_max=True)
        ax.set_title(f"{region_src}地域分布（N={region_n}）", loc="left", fontsize=13,
                     fontweight="bold", color=ct.NAVY, pad=10)
        ct.save_fig(fig, str(charts_dir / "region_dist.png"))
        manifest["region_dist"] = {"path": str(charts_dir / "region_dist.png"), "w": 520,
                                   "h": 60 + 24 * len(region_od),
                                   "caption": f"图 4　{region_src}地域分布（N={region_n}）"}

    # 图5：争点×地域观点热力矩阵（学理模式·描述性个案观点分布）
    issue_region = defaultdict(lambda: Counter())
    for c in cases:
        r = pu.normalize_province(safe_norm(c, "地域"), safe_norm(c, "法院全称"))
        if not r:
            continue
        for focus in (c.get("焦点立场") or {}):
            issue_region[focus][r] += 1
    if issue_region and len({r for cnt in issue_region.values() for r in cnt}) >= 2:
        issues = sorted(issue_region, key=lambda f: -sum(issue_region[f].values()))
        regions = [r for r, _ in Counter(
            {r: sum(issue_region[f].get(r, 0) for f in issues)
             for r in {x for cnt in issue_region.values() for x in cnt}}).most_common()]
        matrix = [[issue_region[f].get(r, 0) for r in regions] for f in issues]
        fig, ax = ct.single(figsize=(max(5.5, 1.1 * len(regions) + 2.5),
                                     max(2.8, 0.5 * len(issues) + 1.5)))
        ct.issue_region_heatmap(ax, issues, regions, matrix)
        ct.save_fig(fig, str(charts_dir / "issue_region.png"))
        manifest["issue_region"] = {"path": str(charts_dir / "issue_region.png"), "w": 520,
                                    "h": 80 + 30 * len(issues),
                                    "caption": "图 5　各争议焦点裁判观点的地域分布（个案观点分布，不构成地域倾向推断）"}

    (charts_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return f"图表与 manifest 已生成（{len(manifest)} 张）。"


if __name__ == "__main__":
    main()
