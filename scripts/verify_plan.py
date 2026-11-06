"""校验排程里的随机坐标是否真的落在各楼栋围栏内（只读，不提交）。

python scripts/verify_plan.py [--limit 10]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwat import api  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="plan.json")
    ap.add_argument("--state", default="state.json")
    ap.add_argument("--limit", type=int, default=10)
    a = ap.parse_args()

    plan = json.loads(Path(a.plan).read_text("utf-8"))
    c = api.QwClient.from_state(a.state)
    user = (c.status().get("user") or {})
    sid = user.get("username")
    now = dt.datetime.now()

    fut = [e for e in plan
           if dt.datetime.strptime(e["target_at"], "%Y-%m-%dT%H:%M:%S") > now][:a.limit]
    print(f"校验接下来 {len(fut)} 节课的随机坐标\n")
    bad = 0
    for e in fut:
        info = c.location_info(e["lat"], e["lng"], sid, e["building"]).get("data") or {}
        ok = bool(info.get("is_valid_location"))
        bad += 0 if ok else 1
        print(f"{'✓' if ok else '✗'} {e['date']} {e['course'][:10]:<12} "
              f"{e['building']:<8} R={e['radius_m']:.0f}m "
              f"偏移={e['offset_m']:.0f}m 实测 distance={info.get('distance')} "
              f"in_range={info.get('in_range')}")
    print(f"\n结果: {len(fut) - bad}/{len(fut)} 在围栏内")
    sys.exit(0 if bad == 0 else 1)


if __name__ == "__main__":
    main()
