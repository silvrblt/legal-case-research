"""
fetch_cases.py —— 直连北大法宝 MCP-over-HTTP 批量检索（证券虚假陈述专题）
─────────────────────────────────────────────
背景：get_case_list 单次返回 25+ 字段全要素，10 条即可达数万 token。若经由 Claude Code
的 MCP 工具通道逐次返回，结果会灌入对话上下文，40 次轮替检索不可承受。本脚本用 .mcp.json
里同一套 endpoint + Bearer Token，以标准库 urllib 直接发 MCP JSON-RPC（Streamable HTTP），
把全部轮替检索在脚本内完成，结果落盘，仅回传精简统计。数据流向与 MCP 工具完全一致（同一
法宝服务器、同一订阅 Token），只是换了调用通道。

用法：
    python3 scripts/securities/fetch_cases.py <research_dir> --words "揭露日,系统风险,..." [--tool get_case_list] [--service case]
    python3 scripts/securities/fetch_cases.py <research_dir> --wordfile path  # 每行一个词

输出：
    <research_dir>/03_raw_cases.json   累加全字段、按 Gid 去重、每条带 _query（命中词列表）
    stdout                              精简统计 + 四家法院判决书清单
"""

import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / ".mcp.json"

SERVICE_KEY = {
    "case": "pkulaw-case-keyword",
    "case-semantic": "pkulaw-case-semantic",
    "law": "pkulaw-law-keyword",
}

TARGET_COURTS = ["北京金融法院", "上海金融法院", "北京市高级人民法院", "上海市高级人民法院"]


def load_endpoint(service="case"):
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    servers = cfg.get("mcpServers", cfg)
    s = servers[SERVICE_KEY[service]]
    return s["url"], s["headers"]["Authorization"]


def parse_response(body: bytes):
    """响应可能是纯 JSON，或 SSE（event: message\\n data: {...}）。统一抠出最后一个 JSON 对象。"""
    text = body.decode("utf-8", errors="replace")
    text_strip = text.lstrip()
    if text_strip.startswith("{"):
        return json.loads(text_strip)
    # SSE: 取所有 data: 行，返回最后一个能解析的 JSON
    last = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                try:
                    last = json.loads(payload)
                except json.JSONDecodeError:
                    pass
    if last is None:
        raise ValueError(f"无法解析响应：{text[:200]}")
    return last


class MCPClient:
    def __init__(self, url, auth):
        self.url = url
        self.auth = auth
        self.session_id = None
        self._rpc_id = 0

    def _headers(self):
        h = {
            "Authorization": self.auth,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _post(self, payload, capture_session=False):
        data = json.dumps(payload).encode("utf-8")
        for attempt in range(5):
            req = urllib.request.Request(self.url, data=data, headers=self._headers(), method="POST")
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    if capture_session:
                        sid = resp.headers.get("Mcp-Session-Id")
                        if sid:
                            self.session_id = sid
                    body = resp.read()
                return parse_response(body) if body else None
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 4:
                    time.sleep(6 * (attempt + 1))  # 限流退避：6s,12s,18s,24s
                    continue
                raise

    def next_id(self):
        self._rpc_id += 1
        return self._rpc_id

    def initialize(self):
        res = self._post({
            "jsonrpc": "2.0",
            "id": self.next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "fetch_cases", "version": "1.0"},
            },
        }, capture_session=True)
        # notifications/initialized（无 id）
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        except Exception:
            pass
        return res

    def call_tool(self, name, arguments):
        res = self._post({
            "jsonrpc": "2.0",
            "id": self.next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        return res


def extract_data(rpc_result):
    """tools/call 返回 result.content[].text（JSON 字符串）→ 解析出 .Data 列表。"""
    if not rpc_result or "result" not in rpc_result:
        return None, rpc_result
    content = rpc_result["result"].get("content", [])
    for item in content:
        if item.get("type") == "text":
            txt = item.get("text", "")
            try:
                obj = json.loads(txt)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "Data" in obj:
                return obj["Data"], obj
            return obj, obj
    return None, rpc_result


def leaf(d):
    if isinstance(d, list):  # 新版 MCP：list（末元素最具体）
        lst = [x for x in d if x]
        return str(lst[-1]) if lst else ""
    if isinstance(d, dict) and d:  # 旧版 MCP：嵌套 dict
        return sorted(d.items(), key=lambda kv: len(kv[0]))[-1][1]
    return ""


def main():
    if len(sys.argv) < 2:
        sys.exit("用法：python3 fetch_cases.py <research_dir> --words \"w1,w2\" | --wordfile f")
    rd = Path(sys.argv[1])
    rd.mkdir(parents=True, exist_ok=True)

    words, tool, service, title = [], "get_case_list", "case", "证券虚假陈述责任纠纷"
    i = 2
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == "--words":
            words = [w.strip() for w in sys.argv[i + 1].split(",") if w.strip()]; i += 2
        elif a == "--wordfile":
            words = [l.strip() for l in Path(sys.argv[i + 1]).read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]; i += 2
        elif a == "--tool":
            tool = sys.argv[i + 1]; i += 2
        elif a == "--service":
            service = sys.argv[i + 1]; i += 2
        elif a == "--title":
            title = sys.argv[i + 1]; i += 2
        else:
            i += 1

    url, auth = load_endpoint(service)
    client = MCPClient(url, auth)
    client.initialize()

    raw_path = rd / "03_raw_cases.json"
    by_gid = {}
    if raw_path.exists():
        for r in json.loads(raw_path.read_text(encoding="utf-8")):
            by_gid[r.get("Gid")] = r

    per_word = {}
    for w in words:
        args = {"title": title, "fulltext": w} if service == "case" else {"text": w}
        try:
            res = client.call_tool(tool, args)
            data, _ = extract_data(res)
        except Exception as e:
            print(f"[ERR] {w}: {e}", file=sys.stderr)
            continue
        if not isinstance(data, list):
            print(f"[WARN] {w}: 无 Data 列表", file=sys.stderr)
            continue
        per_word[w] = len(data)
        for rec in data:
            # 法宝新版不再返回 Gid；以 Url（每文书唯一）兜底合成稳定主键，下游按 Gid 索引无须改动
            gid = rec.get("Gid") or rec.get("Url") or rec.get("CaseFlag")
            if not gid:
                continue
            if not rec.get("Gid"):
                rec["Gid"] = gid
            if gid in by_gid:
                q = by_gid[gid].setdefault("_query", [])
                if w not in q:
                    q.append(w)
            else:
                rec["_query"] = [w]
                by_gid[gid] = rec
        time.sleep(2.5)  # 限速：法宝接口有突发配额，过密会 429

    records = list(by_gid.values())
    raw_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    # 精简统计
    print(f"=== 检索完成：{len(words)} 词，累计去重 {len(records)} 条 ===")
    print("每词命中数：", json.dumps(per_word, ensure_ascii=False))
    print("\n=== 四家法院记录（按文书类型）===")
    target_hits = []
    for r in records:
        court = leaf(r.get("LastInstanceCourt"))
        if any(c in court for c in TARGET_COURTS):
            target_hits.append((court, leaf(r.get("DocumentAttr")), r.get("CaseFlag"),
                                leaf(r.get("TrialStep")), r.get("LastInstanceDate"),
                                r.get("Title"), r.get("Gid")))
    judg = [t for t in target_hits if t[1] == "判决书"]
    print(f"四家法院命中 {len(target_hits)} 条，其中判决书 {len(judg)} 条")
    print("\n-- 判决书清单 --")
    for court, attr, flag, step, date, ttl, gid in sorted(judg, key=lambda x: (x[0], x[4] or "")):
        print(f"  [{court}|{step}|{date}] {flag} | {ttl}")


if __name__ == "__main__":
    main()
