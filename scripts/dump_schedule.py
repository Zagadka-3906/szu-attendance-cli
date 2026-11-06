"""拉全量课表并存成 schedule.json，打印结构概览。

python scripts/dump_schedule.py [--days 60]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwat import api  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--state", default="state.json")
    ap.add_argument("-o", "--out", default="schedule.json")
    a = ap.parse_args()

    c = api.QwClient.from_state(a.state)
    raw = c.schedule(days=a.days)
    Path(a.out).write_text(json.dumps(raw, ensure_ascii=False, indent=1), "utf-8")

    flat = api.flatten(raw)
    print(f"顶层条目 {len(raw)} · 课程实例 {len(flat)} → {a.out}")
    if not flat:
        return
    print("字段:", sorted(flat[0].keys()))
    print("\n样例:")
    print(json.dumps(flat[0], ensure_ascii=False, indent=1)[:900])
    dates = sorted({str(x.get("date"))[:10] for x in flat if x.get("date")})
    if dates:
        print(f"\n日期范围: {dates[0]} → {dates[-1]}（{len(dates)} 天）")
    names = {}
    for x in flat:
        names[x.get("course_name")] = names.get(x.get("course_name"), 0) + 1
    print("\n课程:", ", ".join(f"{k}×{v}" for k, v in sorted(names.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
