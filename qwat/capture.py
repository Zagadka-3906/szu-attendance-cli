"""使用可见浏览器手动登录，并保存 Playwright 兼容的登录态。"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from urllib.parse import quote, urlparse

from . import cas


def login(state: Path = Path("state.json"), timeout_s: int = 300) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit('先运行 python -m pip install -e ".[browser]"，再运行 '
                         'python -m playwright install chromium') from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(user_agent=cas.UA_DESKTOP, locale="zh-CN")
        page = ctx.new_page()
        page.goto(f"{cas.CAS}/login?service={quote(cas.SERVICE, safe='')}&type=user",
                  wait_until="domcontentloaded")
        print(f"[*] 请在浏览器完成密码、验证码或扫码登录，最长等待 {timeout_s} 秒")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            current = urlparse(page.url)
            if current.hostname == "qw.szu.edu.cn" and current.path.startswith("/attendance"):
                response = ctx.request.get(cas.APP + "/cas/status")
                if response.ok and response.json().get("logged_in"):
                    ctx.storage_state(path=str(state))
                    browser.close()
                    print(f"[+] 登录态已保存 → {state.resolve()}")
                    return
            page.wait_for_timeout(1000)
        browser.close()
        raise SystemExit("[-] 登录超时，未保存登录态；请重新运行并完成人工验证")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--login", action="store_true", help="打开浏览器手动登录")
    ap.add_argument("--state", type=Path, default=Path("state.json"))
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()
    if not args.login:
        ap.print_help()
        return
    if args.timeout <= 0:
        ap.error("--timeout 必须大于 0")
    login(args.state, args.timeout)


if __name__ == "__main__":
    main()
