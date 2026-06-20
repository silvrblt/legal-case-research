#!/bin/sh
# 一键安装本仓 git 钩子（已纳入版本管理 .githooks/）。re-clone 后跑一次即可，换机不丢守门。
# 用 git core.hooksPath 指向追踪的 .githooks/，无需复制进 .git/hooks。
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
chmod +x "$DIR/.githooks/"* 2>/dev/null || true
git -C "$DIR" config core.hooksPath .githooks
echo "✓ 已设 core.hooksPath=.githooks —— 本仓护城河 pre-commit 守门已生效"
echo "  （验证：git -C \"$DIR\" config core.hooksPath）"
