"""只读窥探：确认登录态、课表、楼栋坐标、以及「这节课现在能不能签」。

python scripts/peek.py
python scripts/peek.py --at 22.5341 113.9352 --course "示例课程A"
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
    ap.add_argument("--state", default="state.json")
    ap.add_argument("--course", default=None)
    ap.add_argument("--at", nargs=2, type=float, default=None, metavar=("LAT", "LNG"))
    a = ap.parse_args()

    c = api.QwClient.from_state(a.state)

    st = c.status()
    user = st.get("user") or {}
    print(f"[cas] logged_in={st.get('logged_in')}  {user.get('name')} · {user.get('username')}")

    sch = c.schedule()
    print(f"[课表] {len(sch)} 天")
    target = None
    for c_ in (api.current_or_next(sch),):
        if c_:
            print(f"  当前/下一节: {c_.get('course_name')} · {c_.get('classroom')} · "
                  f"{str(c_.get('start_time'))[:5]}-{str(c_.get('end_time'))[:5]} "
                  f"({c_.get('date')})")
            target = c_
    if a.course:
        for day in sch:
            for x in (day.get("courses") or []):
                if x.get("course_name") == a.course:
                    target = {**x, "date": day.get("date")}
                    print(f"  指定课程: {x.get('course_name')} · {x.get('classroom')} · {day.get('date')}")
                    break

    bs = c.buildings()
    print(f"[楼栋] {len(bs)} 座")
    if bs:
        b0 = bs[0]
        print("  字段样例:", json.dumps({k: b0[k] for k in list(b0)[:8]}, ensure_ascii=False)[:200])

    if target:
        b = api.building_of(target, bs)
        pt = api.point_of(b)
        print(f"[匹配] 楼栋={b.get('name') if b else None} 坐标={pt}")

    if a.at:
        lat, lng = a.at
        sid = user.get("username")
        info = c.location_info(lat, lng, sid, (target or {}).get("building_name"))
        print("\n[location-info]", json.dumps(info, ensure_ascii=False)[:600])
        chk = c.check_status(lat, lng, sid,
                             (target or {}).get("student_course_id"),
                             (target or {}).get("session_id"))
        print("[check-signin-status]", json.dumps(chk, ensure_ascii=False)[:800])


if __name__ == "__main__":
    main()
