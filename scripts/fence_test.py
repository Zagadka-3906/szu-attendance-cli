"""试探围栏判定：给定若干坐标，看服务端返回的 distance / in_range / is_valid_location。

python scripts/fence_test.py --building 汇文楼 --points 22.5377,113.935 22.5287,113.935
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwat import api  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--building", default="汇文楼")
    ap.add_argument("--points", nargs="+", required=True, help='"lat,lng" ...')
    ap.add_argument("--state", default="state.json")
    a = ap.parse_args()

    c = api.QwClient.from_state(a.state)
    sid = (c.status().get("user") or {}).get("username")
    for p in a.points:
        lat, lng = (float(x) for x in p.split(","))
        d = c.location_info(lat, lng, sid, a.building).get("data") or {}
        print(f"{p:<24} distance={d.get('distance')!s:<8} "
              f"in_range={d.get('in_range')!s:<6} valid={d.get('is_valid_location')!s:<6} "
              f"status={d.get('status')} · {d.get('message')}")


if __name__ == "__main__":
    main()
