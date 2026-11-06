"""检查 state.json 里登录态的健康度（只打印 cookie 名字与过期时间，不泄露值）。

python scripts/check_state.py [state.json]
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwat import cas  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", nargs="?", type=Path, default=Path("state.json"))
    path = parser.parse_args().state
    if not path.exists():
        print(f"[-] {path} 不存在，先跑 python -m qwat.capture --login")
        return

    data = json.loads(path.read_text("utf-8"))
    cks = data.get("cookies", [])
    now = datetime.datetime.now()
    print(f"[*] {path}：{len(cks)} 个 cookie")
    for c in cks:
        exp = c.get("expires", -1)
        if not exp or exp < 0:
            ts, left = "session", "-"
        else:
            dt = datetime.datetime.fromtimestamp(exp)
            days = (dt - now).total_seconds() / 86400
            ts = dt.strftime("%Y-%m-%d %H:%M")
            left = f"{days:.1f} 天"
        print(f"    {c['name']:<24} {c.get('domain',''):<26} 过期={ts:<18} 剩余={left}")

    print()
    if not cas.castgc(cas.load_state(path)):
        print("[-] 没有 CASTGC：只能靠 session cookie 硬撑，过期后必须重新 --login")
        return

    s = cas.load_state(path)
    try:
        t = cas.mint_ticket(s)
        print("[+] CASTGC 有效，成功取得服务票据（不显示票据值）")
        r = s.get(cas.CALLBACK, params={"redirect": "/attendance", "source": "user",
                                        "ticket": t}, allow_redirects=False, timeout=20)
        print(f"[+] 回灌 callback：{r.status_code} → {r.headers.get('Location','')[:80]}")
    except cas.NotLoggedIn as exc:
        print(f"[-] {exc}")


if __name__ == "__main__":
    main()
