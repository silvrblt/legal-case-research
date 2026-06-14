"""
normalize_cases.py —— 拍平 MCP 嵌套字段 + 派生确定性字段（通用/散案管道）
─────────────────────────────────────────────
用法：python3 scripts/general/normalize_cases.py <research_dir>
输入：<research_dir>/05_enriched_cases.json （Claude Code 已做完判断性编码）
输出：就地补全每条记录的 _norm 字段（法院层级/地域/裁判年份/拍平案由等）

本脚本只做规则能确定的派生，不做需要法律判断的编码（那部分由 Claude Code 完成）。
公共派生函数集中在 scripts/common/pkulaw_utils.py。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from pkulaw_utils import (  # noqa: E402
    derive_court_level, flatten_court, flatten_leaf, derive_year, normalize_province,
)


def normalize_one(case: dict, raw_idx: dict) -> dict:
    raw = raw_idx.get(case.get("Gid"), {})

    def pick(key):
        v = case.get(key)
        if v not in (None, "", [], {}):
            return v
        return raw.get(key)

    province, court = flatten_court(pick("LastInstanceCourt"))
    norm = {
        "法院全称": court,
        "法院层级": derive_court_level(court),
        "地域": normalize_province(province, court),
        "裁判年份": derive_year(pick("LastInstanceDate") or ""),
        "案由": flatten_leaf(pick("Category")),
        "案件等级": flatten_leaf(pick("CaseGrade")) or "普通案例",
    }
    case["_norm"] = norm
    return case


def main():
    if len(sys.argv) < 2:
        sys.exit("用法：python3 scripts/general/normalize_cases.py <research_dir>")
    research_dir = Path(sys.argv[1])
    path = research_dir / "05_enriched_cases.json"
    if not path.exists():
        sys.exit(f"未找到 {path}")

    # 建 03 原始记录索引用于字段回退
    raw_path = research_dir / "03_raw_cases.json"
    raw_idx = {}
    if raw_path.exists():
        for r in json.loads(raw_path.read_text(encoding="utf-8")):
            if r.get("Gid"):
                raw_idx[r["Gid"]] = r

    cases = json.loads(path.read_text(encoding="utf-8"))
    for c in cases:
        normalize_one(c, raw_idx)

    path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已规范化 {len(cases)} 条，写回 {path}")


if __name__ == "__main__":
    main()
