"""打印排程表：按时间顺序列出待执行课次。

python scripts/show_plan.py [--limit 20] [--all]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="plan.json")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()

    plan = json.loads(Path(a.plan).read_text("utf-8"))
    now = dt.datetime.now()
    pend = [e for e in plan if e["state"] == "pending"]
    pend.sort(key=lambda e: e["target_at"])
    fut = [e for e in pend
           if dt.datetime.strptime(e["target_at"], "%Y-%m-%dT%H:%M:%S") > now]

    print(f"总 {len(plan)} 条 · 待执行 {len(pend)} 条 · 未来 {len(fut)} 条\n")
    print(f"{'签到时刻':<20}{'课程':<18}{'教室':<18}{'开始':<7}{'提前':<6}{'偏移':<8}坐标")
    for e in (pend if a.all else fut)[:a.limit]:
        print(f"{e['target_at']:<20}{(e['course'] or '')[:16]:<18}"
              f"{(e['classroom'] or '')[:16]:<18}{e['start']:<7}"
              f"{e['lead_min']:<6}{e['offset_m']:<8}{e['lat']},{e['lng']}")

    today = [e for e in plan if e["date"] == now.strftime("%Y-%m-%d")]
    print(f"\n今天 {len(today)} 节：" + "、".join(
        f"{e['start']} {e['course']}({e['state']})" for e in today))


if __name__ == "__main__":
    main()
