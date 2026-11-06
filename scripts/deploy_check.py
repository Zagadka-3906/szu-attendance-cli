"""部署后自检：从这台服务器出发，把整条链路验一遍。

  python scripts/deploy_check.py

检查项：出口 IP / 时区 / DNS / 站点可达性 / 免登录接口 / 登录态 / 登录能力 / 排程
"""
from __future__ import annotations

import datetime as dt
import json
import platform
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwat import api, auth, cas  # noqa: E402

OK, BAD, WARN = "✅", "❌", "⚠️"


def sh(cmd: str) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=12).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def main() -> None:
    fails: list[str] = []
    print(f"平台 {platform.platform()} · Python {platform.python_version()}")

    # 1) 出口 IP
    ipinfo = sh("curl -sS -m 10 https://myip.ipip.net") or sh("curl -sS -m 10 https://api.ip.sb/geoip")
    print(f"[网络] 出口 IP: {ipinfo[:120] or '取不到（无外网？）'}")
    if not ipinfo:
        fails.append("没有外网出口")

    # 2) 时区
    off = dt.datetime.now().astimezone().utcoffset()
    tz = dt.datetime.now().astimezone().tzname()
    print(f"[时区] {tz} · UTC{off} · 本地 {dt.datetime.now():%Y-%m-%d %H:%M}")
    if off != dt.timedelta(hours=8):
        fails.append("时区不是 UTC+8，排程会全错")

    # 3) DNS
    for host in ("qw.szu.edu.cn", "authserver.szu.edu.cn"):
        try:
            ip = socket.gethostbyname(host)
            print(f"[DNS ] {host:<24} → {ip}")
        except Exception as exc:  # noqa: BLE001
            print(f"[DNS ] {host} 解析失败: {exc}")
            fails.append(f"{host} 解析失败")

    # 4) 可达性（免登录接口）
    import requests
    try:
        r = requests.get("https://qw.szu.edu.cn/attendance/api/v1/buildings", timeout=15)
        n = len((r.json().get("data") or [])) if r.headers.get("content-type", "").startswith("application/json") else 0
        print(f"[站点] /api/v1/buildings → {r.status_code}，{n} 座楼")
        if r.status_code != 200:
            fails.append(f"应用接口返回 {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        print(f"[站点] 访问失败: {exc}")
        fails.append("访问不了应用接口")

    # 5) 登录能力
    has_creds = auth.CREDS.exists() or Path("config.yaml").exists()
    print(f"[凭据] {'creds.bin 存在' if auth.CREDS.exists() else '无 creds.bin'}"
          f" · config.yaml {'存在' if Path('config.yaml').exists() else '缺失'}")
    if has_creds:
        try:
            sid, pwd = auth.load_creds()
            print(f"        可读出 {sid}（密码 {len(pwd)} 位）")
            t0 = dt.datetime.now()
            s = auth.login(sid, pwd)
            print(f"[登录] 密码登录{'成功' if cas.castgc(s) else '失败'} "
                  f"耗时 {(dt.datetime.now()-t0).total_seconds():.2f}s")
            cas.save_cookies(s, Path("state.json"))
            print(f"[铸票] {cas.mint_ticket(s)[:22]}…")
        except auth.NeedCaptcha as exc:
            print(f"[登录] {WARN} 需要验证码：{exc}")
            print("        对策：在能开浏览器的机器上跑 python -m qwat.capture --login，"
                  "把 state.json 传到服务器")
        except Exception as exc:  # noqa: BLE001
            print(f"[登录] {BAD} {type(exc).__name__}: {exc}")
            fails.append("登录失败")
    else:
        print(f"[登录] {WARN} 没有凭据，先跑 python scripts/save_creds.py")

    # 6) 登录态可用性
    if Path("state.json").exists():
        try:
            c = api.QwClient.from_state(Path("state.json"))
            st = c.status()
            print(f"[会话] logged_in={st.get('logged_in')} "
                  f"{(st.get('user') or {}).get('name')}")
            if not st.get("logged_in"):
                fails.append("会话无效")
        except Exception as exc:  # noqa: BLE001
            print(f"[会话] {BAD} {exc}")
            fails.append("会话检查失败")

    # 7) 排程
    plan = Path("plan.json")
    if plan.exists():
        data = json.loads(plan.read_text("utf-8"))
        pend = [e for e in data if e["state"] == "pending"]
        pend.sort(key=lambda e: e["target_at"])
        print(f"[排程] {len(data)} 条，待执行 {len(pend)} 条")
        if pend:
            print(f"        下一枪 {pend[0]['target_at']} {pend[0]['course']}")
    else:
        print(f"[排程] {WARN} 没有 plan.json，跑 python -m qwat.autoplan --build")
        fails.append("没有排程")

    print()
    if fails:
        print(f"{BAD} 未通过 {len(fails)} 项：" + "；".join(fails))
        sys.exit(1)
    print(f"{OK} 全部通过，可以托管")


if __name__ == "__main__":
    main()
