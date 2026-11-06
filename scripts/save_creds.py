"""保存登录凭据（密码用 getpass 输入，不落日志、不进命令行历史）。

python scripts/save_creds.py            # 交互输入
python scripts/save_creds.py --check    # 只验一次当前凭据能否登录
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwat import auth, cas  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", help="学号；缺省交互输入")
    ap.add_argument("--check", action="store_true", help="用已保存凭据验证登录")
    ap.add_argument("--state", default="state.json")
    a = ap.parse_args()

    if a.check:
        sid, pwd = auth.load_creds()
        print(f"[*] 用已保存凭据登录 {sid} …")
        s = auth.login(sid, pwd)
        cas.save_cookies(s, Path(a.state))
        print("[+] 登录成功，CASTGC =", (cas.castgc(s) or "")[:20], "…")
        return

    sid = a.id or input("学号: ").strip()
    pwd = getpass.getpass("密码（输入时不显示）: ")
    if not sid or not pwd:
        sys.exit("[-] 学号或密码为空")

    print("[*] 先验证一次，确认凭据可用 …")
    try:
        s = auth.login(sid, pwd)
    except auth.NeedCaptcha as exc:
        print(f"[-] {exc}")
        print("    稍后再试，或先在浏览器里手动登录一次降低风控等级。")
        sys.exit(2)
    except auth.LoginError as exc:
        print(f"[-] {exc}")
        sys.exit(1)

    auth.save_creds(sid, pwd)
    cas.save_cookies(s, Path(a.state))
    print(f"[+] 凭据已验证并保存 → {auth.CREDS.resolve()}（DPAPI 加密，仅当前 Windows 账户可解）")
    print(f"[+] 登录态已刷新 → {Path(a.state).resolve()}")


if __name__ == "__main__":
    main()
