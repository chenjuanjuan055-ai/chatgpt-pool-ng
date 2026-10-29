#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按探测结果批量启用/停用邮箱。

规则：
  registered_2fa    → 停用（验证器在别人手里，抢不回来）
  registered_no_2fa → 启用（邮箱在我们手里，随时能接管）
  unregistered      → 启用（还没注册，留着）
  unknown           → 不动，单独列出待复查

用法：
    python scripts/apply_probe_results.py _tmp/probe/*.jsonl            # 预览
    python scripts/apply_probe_results.py _tmp/probe/*.jsonl --apply    # 执行
"""
from __future__ import annotations

import argparse
import glob
import json
import urllib.request
from pathlib import Path

MAIL_API = "http://127.0.0.1:19878"
TOOL_DIR = Path(r"D:\xi\msa-mail-tool")

DESIRED = {
    "registered_2fa": "disable",
    "registered_no_2fa": "enable",
    "unregistered": "enable",
    "deleted": "enable",
}


def load_results(patterns: list[str]) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for pattern in patterns:
        for path in glob.glob(pattern):
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                email = str(item.get("email") or "").lower()
                if not email:
                    continue
                current = merged.get(email)
                if current is None or current.get("state") in (None, "unknown"):
                    merged[email] = item
    return merged


def current_disabled() -> set[str]:
    data = json.loads((TOOL_DIR / "disabled.json").read_text(encoding="utf-8"))
    return {str(email).lower() for email in data.get("emails") or []}


def toggle(email: str) -> bool:
    req = urllib.request.Request(
        MAIL_API + "/api/toggle_card",
        data=json.dumps({"email": email}).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8") or "{}")
    return bool(payload.get("disabled"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("patterns", nargs="+")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", default="probe_report.md")
    parser.add_argument("--keep", default="", help="永不改动的邮箱，逗号分隔（自己开的号）")
    args = parser.parse_args()

    keep = {item.strip().lower() for item in args.keep.split(",") if item.strip()}
    results = load_results(args.patterns)
    for email in list(results):
        if email in keep:
            results.pop(email)
    disabled_now = current_disabled()

    counts = {"registered_2fa": 0, "registered_no_2fa": 0, "unregistered": 0, "unknown": 0}
    plan: list[tuple[str, str, bool]] = []
    for email, item in sorted(results.items()):
        state = str(item.get("state") or "unknown")
        counts[state] = counts.get(state, 0) + 1
        action = DESIRED.get(state)
        if action is None:
            continue
        is_disabled = email in disabled_now
        want_disabled = action == "disable"
        if is_disabled != want_disabled:
            plan.append((email, state, want_disabled))

    print("探测结果：", json.dumps(counts, ensure_ascii=False))
    print(f"需要变更：{len(plan)} 个（停用 {sum(1 for _,_,d in plan if d)} / 启用 {sum(1 for _,_,d in plan if not d)}）")
    for email, state, want_disabled in plan[:10]:
        print(f"  {'停用' if want_disabled else '启用'} {email}  [{state}]")
    if len(plan) > 10:
        print(f"  …… 其余 {len(plan) - 10} 个")

    unknown = [email for email, item in results.items() if item.get("state") not in DESIRED]
    if unknown:
        print(f"未知状态待复查：{len(unknown)} 个")

    if not args.apply:
        print("\n预览模式，加 --apply 执行")
        return 0

    changed = 0
    for email, state, want_disabled in plan:
        try:
            now_disabled = toggle(email)
            if now_disabled == want_disabled:
                changed += 1
            else:
                print(f"  状态未生效：{email}")
        except Exception as exc:
            print(f"  切换失败：{email} {exc}")

    report = [
        "# 邮箱探测报告",
        "",
        f"- 已探测：{len(results)}",
        f"- 已绑定 2FA（停用）：{counts.get('registered_2fa', 0)}",
        f"- 已注册未绑 2FA（启用）：{counts.get('registered_no_2fa', 0)}",
        f"- 未注册（启用）：{counts.get('unregistered', 0)}",
        f"- 状态未知（未改动）：{counts.get('unknown', 0)}",
        f"- 本次实际切换：{changed}",
        "",
    ]
    if unknown:
        report.append("## 待复查")
        report.extend(f"- {email}" for email in sorted(unknown))
    Path(args.report).write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"已切换 {changed} 个，报告写入 {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
