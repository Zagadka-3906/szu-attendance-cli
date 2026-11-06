"""Optional Playwright browser check-in. HTTP calls live in qwat.sign."""
from __future__ import annotations

import json
from pathlib import Path

from . import cas

SHIM_PATH = Path(__file__).with_name("wxshim.js")


def _shim_js(coords: dict) -> str:
    return (SHIM_PATH.read_text("utf-8")
            .replace("__LAT__", repr(float(coords["lat"])))
            .replace("__LNG__", repr(float(coords["lng"])))
            .replace("__ACC__", repr(float(coords.get("accuracy", 12))))
            .replace("__ADDRESS__", json.dumps(coords.get("address") or "广东省深圳市南山区",
                                               ensure_ascii=False)[1:-1]))


def _click_any(page, names: list[str], timeout: int = 3000) -> str | None:
    """优先点真正的按钮元素，避免点到同名字的提示文案上。"""
    for n in names:
        for build in (
            lambda: page.get_by_role("button", name=n, exact=False),
            lambda: page.locator(f"text={n}"),
        ):
            try:
                loc = build().first
                if not loc.count() or not loc.is_visible() or not loc.is_enabled():
                    continue
                loc.click(timeout=timeout)
                return n
            except Exception:
                continue
    return None


def _wait_text_gone(page, needles: list[str], timeout_s: float = 12.0) -> bool:
    import time as _t
    end = _t.time() + timeout_s
    while _t.time() < end:
        body = page.inner_text("body")
        if not any(k in body for k in needles):
            return True
        page.wait_for_timeout(400)
    return False


def checkin_browser(coords: dict, state: Path = Path("state.json"),
                    photo: str | None = None, headless: bool = True,
                    url: str | None = None, ua: str | None = None,
                    keep_open: bool = False, dry_run: bool = False) -> dict:
    from playwright.sync_api import sync_playwright

    posts: list[dict] = []
    logs: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=[
            "--no-sandbox",
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            "--disable-blink-features=AutomationControlled",
        ])
        ctx = browser.new_context(
            storage_state=str(state) if Path(state).exists() else None,
            user_agent=ua or cas.UA_DESKTOP,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1280, "height": 900},
            permissions=["geolocation", "camera", "microphone"],
            geolocation={"latitude": coords["lat"], "longitude": coords["lng"],
                         "accuracy": coords.get("accuracy", 12)},
        )
        ctx.add_init_script(_shim_js(coords))          # 关键：先手注入
        page = ctx.new_page()

        cdp = ctx.new_cdp_session(page)
        cdp.send("Emulation.setGeolocationOverride", {
            "latitude": coords["lat"], "longitude": coords["lng"],
            "accuracy": coords.get("accuracy", 12),
        })
        cdp.send("Emulation.setTimezoneOverride", {"timezoneId": "Asia/Shanghai"})

        def on_resp(r):
            if r.request.method in ("POST", "PUT") and "qw.szu.edu.cn" in r.url:
                try:
                    body = r.text()[:600]
                except Exception:
                    body = ""
                posts.append({"url": r.url, "status": r.status,
                              "req": (r.request.post_data or "")[:800], "resp": body})
                print(f"  → {r.status} {r.url[:100]}")

        page.on("response", on_resp)
        page.on("console", lambda m: logs.append(m.text[:200]))

        page.goto(url or (cas.APP + "/"), wait_until="networkidle")
        page.wait_for_timeout(2000)
        page.screenshot(path="step1_landing.png", full_page=True)

        # 触发重新取点：此刻页面读到的是 shim 注入的坐标
        clicked = _click_any(page, ["重新获取定位", "重新定位", "获取定位"])
        _wait_text_gone(page, ["定位服务未就绪", "位置未知"], timeout_s=10)
        page.wait_for_timeout(800)
        page.screenshot(path="step2_geo.png", full_page=True)

        body = page.inner_text("body")
        geo_ok = ("位置未知" not in body) and ("定位服务未就绪" not in body)
        print(f"[geo] {'OK' if geo_ok else 'FAIL'} · 重取定位按钮={clicked}")

        if photo and not dry_run:
            try:
                page.set_input_files("input[type=file]", photo, timeout=4000)
                page.wait_for_timeout(1500)
            except Exception as exc:
                logs.append(f"photo: {exc}")

        submit = None
        if not dry_run and geo_ok:
            submit = _click_any(page, ["上传照片并签到", "确认签到", "立即签到", "提交签到", "签到"])
        page.wait_for_timeout(3500)
        page.screenshot(path="step3_result.png", full_page=True)
        after = page.inner_text("body")

        if keep_open:
            input("回车关闭浏览器…")

        ctx.storage_state(path=str(state))
        browser.close()

    hit = any(k in after for k in ("签到成功", "已签到", "签到完成", "提交成功"))
    fail = any(k in after for k in ("签到失败", "提交失败", "不在签到范围", "超出范围"))
    return {
        "ok": hit and not fail,
        "dry_run": dry_run,
        "geo_ok": geo_ok,
        "clicked": {"recheck": clicked, "submit": submit},
        "posts": posts,
        "screenshots": ["step1_landing.png", "step2_geo.png", "step3_result.png"],
        "tail_text": after[-600:],
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="浏览器辅助签到；HTTP 签到请使用 qwat.sign")
    ap.add_argument("--at", nargs=2, type=float, required=True, metavar=("LAT", "LNG"))
    ap.add_argument("--state", type=Path, default=Path("state.json"))
    ap.add_argument("--photo")
    ap.add_argument("--head", action="store_true", help="显示浏览器窗口")
    ap.add_argument("--keep-open", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="仅检查页面定位，不上传照片或点击提交")
    ap.add_argument("--yes", action="store_true", help="确认点击页面签到按钮")
    args = ap.parse_args()
    if not args.dry_run and not args.yes:
        ap.error("实际提交需要 --yes，请先用 --dry-run 检查页面")
    if not args.state.is_file():
        ap.error("登录态不存在，请先使用 qwat.capture --login")
    if not args.dry_run and (not args.photo or not Path(args.photo).is_file()):
        ap.error("实际提交需要 --photo 指定存在的照片")
    result = checkin_browser({"lat": args.at[0], "lng": args.at[1]},
                             state=args.state, photo=args.photo,
                             headless=not args.head, keep_open=args.keep_open,
                             dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if (result["geo_ok"] if args.dry_run else result["ok"]) else 2)


if __name__ == "__main__":
    main()
