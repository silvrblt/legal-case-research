"""
pkulaw_utils.py —— 北大法宝类案研究脚本的公共工具层
─────────────────────────────────────────────
散案管道（scripts/general/*）与集团案管道（scripts/securities/*，证券虚假陈述为内置示范）共享的确定性派生与字段处理函数，
集中在此一处维护，避免同一函数 copy 在多个脚本里、修一处漏三处（如"金融法院"层级档）。

被 scripts/general/* 与 scripts/securities/* 通过下面的方式引入：
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
    from pkulaw_utils import clean_url, derive_court_level, ...

只放"规则可定"的派生；任何需要法律判断的编码由 Claude Code 完成。
"""

import json
import re
from pathlib import Path


def load(path):
    """读 JSON，文件不存在则返回空列表。"""
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def clean_url(u):
    """MCP 的 Url 形如 '[北大法宝](https://...)'，抠出裸链接；裸链接原样返回。
    写入 Excel/报告前务必清洗，否则单元格里是不可点击的 Markdown 文本。"""
    if not isinstance(u, str):
        return u or ""
    m = re.search(r"\((https?://[^)]+)\)", u)
    if m:
        return m.group(1)
    m = re.search(r"https?://\S+", u)
    return m.group(0) if m else u


def derive_court_level(court_name: str) -> str:
    """从法院全称推断层级。含"金融法院"专门法院档（北京/上海金融法院为中级层级专门法院，
    其全称不含"人民法院"四字，若无此分支会被误归到"其他"）。"""
    if not court_name:
        return "未知"
    if "最高人民法院" in court_name:
        return "最高人民法院"
    if "金融法院" in court_name:
        return "金融法院"
    if "知识产权法院" in court_name:
        return "知产法院"
    if "互联网法院" in court_name:
        return "互联网法院"
    if "高级人民法院" in court_name:
        return "高院"
    if "中级人民法院" in court_name or "中院" in court_name:
        return "中院"
    if "人民法院" in court_name:
        return "基层"
    return "其他"


def derive_region(court_name: str) -> str:
    """从法院全称推断省级地域（全国化；学理实战修复——原版只认京沪，
    导致全国样本的法院地字段大量为空、地域统计失真）。"""
    if not court_name:
        return ""
    full = normalize_province("", court_name)
    if full:
        # 京沪保留简称（历史口径兼容），其余返回规范全称
        return {"北京市": "北京", "上海市": "上海"}.get(full, full)
    return ""


def flatten_court(last_instance_court) -> tuple:
    """LastInstanceCourt 取 (省级地域, 最末级法院全称)。
    兼容两种 MCP 格式：① 旧版 {层级码: 名称} 嵌套对象（键按长度排序：最短=省级，最长=具体法院）；
    ② 新版 list（按省→辖区→具体法院顺序排列，首元素=省级，末元素=具体法院，按位置取，勿按长度排）。"""
    if isinstance(last_instance_court, list):
        lst = [str(x) for x in last_instance_court if x]
        if not lst:
            return "", ""
        return lst[0], lst[-1]
    if not isinstance(last_instance_court, dict) or not last_instance_court:
        return "", ""
    items = sorted(last_instance_court.items(), key=lambda kv: len(kv[0]))
    return items[0][1], items[-1][1]


def flatten_leaf(nested) -> str:
    """对 Category / CaseGrade / DocumentAttr 取最末级（最具体）的值。
    兼容旧版嵌套 dict（键最长者）与新版 MCP 的 list（末元素最具体）。"""
    if isinstance(nested, list):
        lst = [str(x) for x in nested if x]
        return lst[-1] if lst else ""
    if not isinstance(nested, dict) or not nested:
        return ""
    items = sorted(nested.items(), key=lambda kv: len(kv[0]))
    return items[-1][1]


def derive_year(date_str: str) -> str:
    """从裁判日期（YYYY.MM.DD 或 YYYY-MM-DD）取年份。"""
    if not date_str:
        return ""
    m = re.match(r"(\d{4})", str(date_str).strip())
    return m.group(1) if m else ""


def build_raw_index(research_dir) -> dict:
    """读取 03_raw_cases.json，建 {Gid: 原始MCP记录} 索引（按 Gid 去重、保留首次出现），
    用于下游脚本的字段回退与附录/平行判决来源。"""
    idx = {}
    for r in load(Path(research_dir) / "03_raw_cases.json"):
        gid = r.get("Gid")
        if gid and gid not in idx:
            idx[gid] = r
    return idx


def field(case: dict, raw_idx: dict, key: str, default=""):
    """优先取 case 自身字段；为空则按 Gid 回退到 03 原始记录。"""
    val = case.get(key)
    if val not in (None, "", [], {}):
        return val
    raw = raw_idx.get(case.get("Gid"))
    if raw:
        rv = raw.get(key)
        if rv not in (None, "", [], {}):
            return rv
    return default


_PROVINCE_SHORT = {
    "北京": "北京市", "上海": "上海市", "天津": "天津市", "重庆": "重庆市",
    "内蒙古": "内蒙古自治区", "广西": "广西壮族自治区", "西藏": "西藏自治区",
    "宁夏": "宁夏回族自治区", "新疆": "新疆维吾尔自治区",
}
_PROVINCE_NAMES = ["河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽",
                   "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "海南",
                   "四川", "贵州", "云南", "陕西", "甘肃", "青海"]


def normalize_province(prov: str, court: str = "") -> str:
    """规范化省级地域。LastInstanceCourt 的短键有时是法院类型（如'金融法院'）而非省份，
    或为空；此时按法院全称前缀归省（'上海金融法院'→'上海市'）。统计/出图前务必过此函数，
    否则地域分布会出现'金融法院'这类伪地域。"""
    if prov and (prov.endswith(("省", "市", "自治区")) and "法院" not in prov):
        return prov
    name = court or prov or ""
    for short, full in _PROVINCE_SHORT.items():
        if name.startswith(short):
            return full
    for p in _PROVINCE_NAMES:
        if name.startswith(p):
            return p + "省"
    return prov if prov and "法院" not in prov else ""


def normalize_caseno(s: str) -> str:
    """规范化案号用于比对：统一全角括号、去除所有空白与不可见字符。
    verify_report（反幻觉校验）与 check_coverage（名录核对）共用。"""
    if not s:
        return ""
    s = s.replace("(", "（").replace(")", "）")
    return re.sub(r"\s+", "", s.strip())


COMPANY_RE = re.compile(
    r"([一-龥（）()A-Za-z0-9]{2,}?"
    r"(?:股份有限公司|有限责任公司|有限公司|集团股份|集团|银行|证券|保险))"
)


def derive_issuer(title: str) -> str:
    """从案件名兜底抽取涉案上市公司（取被告侧公司名，启发式）。
    证券案标题多为"×××与○○股份有限公司证券虚假陈述责任纠纷"，取最后一个公司名。"""
    if not title:
        return ""
    cands = COMPANY_RE.findall(title)
    return cands[-1] if cands else ""
