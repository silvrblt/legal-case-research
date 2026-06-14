"""
divergence.py —— 裁判观点分歧检测（数据层，散案/集团案两类管道共用）
─────────────────────────────────────────────
把"同一争点、对立立场并存"从统计副产品升级为一等产出：
  · 通用工作流：读 `焦点立场` 的 立场；集团诉讼工作流：读 `问题观点` 的 倾向标签。
  · 对每个出现 ≥2 个实质立场的争点，输出 立场分布 / 各立场代表案号 / 地域观察 /
    地域闸门判定（statistical claim 仍受 stats_guard.divergence_gate 约束）。

法律依据呼应：《最高人民法院关于统一法律适用加强类案检索的指导意见（试行）》
（法发〔2020〕24号）第十一条——检索到的类案存在法律适用不一致的，依分歧解决机制处理。
本模块就是把"第十一条情形"显性化为可交付的数据。

下游：06_analytics.json 的 `分歧地图` 节、Excel「裁判分歧清单」sheet、
报告综合分析的分歧小节（呈现层规范见 ROADMAP 二期）。
"""
from __future__ import annotations

from collections import Counter, defaultdict

import stats_guard as sg

# 不构成实质立场的取值（出现这些不算"分歧"）
NON_SUBSTANTIVE = {"未评述", "未涉及", "—", "", None}


def build_divergence(cases, get_positions, get_region, get_caseflag, max_reps=2) -> dict:
    """
    cases: 案件列表。
    get_positions(case) -> {争点名: 立场字符串}
    get_region(case)    -> 地域（用于分组观察与闸门）
    get_caseflag(case)  -> 案号（代表案引用）
    返回 {争点名: {...}}，仅收录存在对立（≥2 个实质立场）的争点。
    """
    by_issue = defaultdict(list)  # 争点 -> [(立场, case)]
    for c in cases:
        for issue, stance in (get_positions(c) or {}).items():
            by_issue[issue].append((stance, c))

    out = {}
    for issue, pairs in by_issue.items():
        substantive = [(s, c) for s, c in pairs if s not in NON_SUBSTANTIVE]
        stances = Counter(s for s, _ in substantive)
        if len(stances) < 2:
            continue

        reps = {}
        for stance in stances:
            flags = [get_caseflag(c) for s, c in substantive if s == stance and get_caseflag(c)]
            reps[stance] = flags[:max_reps]

        region_obs = defaultdict(Counter)
        for s, c in substantive:
            r = get_region(c)
            if r:
                region_obs[r][s] += 1
        region_counts = {r: sum(cnt.values()) for r, cnt in region_obs.items()}
        gate = (sg.divergence_gate(region_counts) if region_counts
                else {"report": False, "phrasing": ""})

        out[issue] = {
            "立场分布": dict(stances.most_common()),
            "代表案": reps,
            "地域观察": {r: dict(cnt) for r, cnt in region_obs.items()},
            "地域闸门": {"report": bool(gate.get("report")),
                     "phrasing": gate.get("phrasing") or ""},
            "措辞": ("对立观点并存；分组样本达阈值，可作地域比较"
                   if gate.get("report") else
                   "对立观点并存（个案观点分布；分组样本不足，不构成地域/层级倾向结论）"),
        }
    return out
