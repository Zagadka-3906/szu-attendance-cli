"""诊断：某个 session_id 的资格查询返回什么。

python scripts/check_one.py --sid "<server_session_id>" --enr <enrollment_id> --at <lat>,<lng>
上述参数为占位符；请替换为当前账号的服务端响应值，不要公开真实课次或选课记录 ID。
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
    ap.add_argument("--sid", required=True)
    ap.add_argument("--enr", type=int, default=None)
    ap.add_argument("--at", required=True, help="lat,lng")
    ap.add_argument("--state", default="state.json")
    a = ap.parse_args()

    lat, lng = (float(x) for x in a.at.split(","))
    c = api.QwClient.from_state(a.state)
    user = c.status().get("user") or {}
    res = c.check_status(lat, lng, user.get("username"), a.enr, a.sid)
    print(json.dumps(res, ensure_ascii=False, indent=1)[:2000])


if __name__ == "__main__":
    main()
