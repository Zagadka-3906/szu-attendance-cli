"""页面层诊断：定位到底卡在哪一层。

用法：
  python scripts/diagnose_page.py                       # 用 state.json 打开签到页
  python scripts/diagnose_page.py --no-shim             # 看原始失败现场
  python scripts/diagnose_page.py --lat 22.53 --lng 113.93

输出四段：
  1. 页面加载了哪些定位相关脚本（jweixin / AMap / qq.maps / 自研）
  2. window.wx 是否存在、navigator.geolocation 是否可用
  3. 控制台报错与失败的 XHR
  4. 弹窗里的实际文案（定位服务未就绪 / 位置未知 …）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwat import cas  # noqa: E402

SHIM = Path(__file__).resolve().parents[1] / "qwat" / "wxshim.js"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="state.json")
    ap.add_argument("--lat", type=float, default=22.5333)
    ap.add_argument("--lng", type=float, default=113.9333)
    ap.add_argument("--acc", type=float, default=12)
    ap.add_argument("--no-shim", action="store_true")
    ap.add_argument("--head", action="store_true", help="有头模式，可肉眼观察")
    ap.add_argument("--seconds", type=int, default=12)
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright

    logs: list[str] = []
    fails: list[str] = []
    scripts: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not a.head, args=["--no-sandbox"])
        ctx = browser.new_context(
            storage_state=a.state if Path(a.state).exists() else None,
            user_agent=cas.UA_DESKTOP,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            permissions=["geolocation", "camera"],
            geolocation={"latitude": a.lat, "longitude": a.lng, "accuracy": a.acc},
        )
        cdp = ctx.new_cdp_session(ctx.new_page())
        cdp.send("Emulation.setGeolocationOverride",
                 {"latitude": a.lat, "longitude": a.lng, "accuracy": a.acc})

        page = ctx.new_page()
        page.on("console", lambda m: logs.append(f"[{m.type}] {m.text}"[:300]))
        page.on("pageerror", lambda e: fails.append(f"[pageerror] {e}"[:300]))
        page.on("request", lambda r: scripts.append(r.url))
        page.on("requestfailed", lambda r: fails.append(f"[reqfail] {r.url[:120]} {r.failure}"))

        if not a.no_shim and SHIM.exists():
            js = SHIM.read_text("utf-8").replace("__LAT__", str(a.lat)) \
                .replace("__LNG__", str(a.lng)).replace("__ACC__", str(a.acc)) \
                .replace("__ADDRESS__", "广东省深圳市南山区")
            page.add_init_script(js)

        page.goto(cas.APP + "/", wait_until="networkidle")
        page.wait_for_timeout(a.seconds * 1000)

        state = page.evaluate("""() => {
            const r = {};
            r.hasWx = typeof window.wx !== 'undefined';
            r.hasWxGetLocation = !!(window.wx && window.wx.getLocation);
            r.hasJweixin = !!window.__wxjs_environment || /jweixin|wx\\.js/.test(
                Array.from(document.scripts).map(s => s.src).join(' '));
            r.shim = window.__QW_GEO_SHIM__ || null;
            r.secure = window.isSecureContext;
            return r;
        }""")

        geo = page.evaluate("""() => new Promise(res => {
            if (!navigator.geolocation) return res({error: 'no api'});
            navigator.geolocation.getCurrentPosition(
                p => res({ok: true, lat: p.coords.latitude, lng: p.coords.longitude,
                          acc: p.coords.accuracy}),
                e => res({ok: false, code: e.code, msg: e.message}),
                {timeout: 4000});
        })""")

        body = page.inner_text("body")[:1500]
        page.screenshot(path="diagnose.png", full_page=True)
        ctx.storage_state(path=a.state) if Path(a.state).exists() else None
        browser.close()

    print("=" * 62)
    print("1) 页面状态")
    for k, v in state.items():
        print(f"   {k:16} {v}")
    print("\n2) navigator.geolocation 实测")
    print("  ", json.dumps(geo, ensure_ascii=False))
    print("\n3) 定位相关请求")
    for u in [x for x in scripts if any(k in x for k in
              ("jweixin", "amap", "qq.map", "location", "geo", "签到", "checkin"))][:12]:
        print("   ", u[:140])
    print("\n4) 控制台")
    for line in [x for x in logs if any(k in x.lower() for k in
                 ("geo", "location", "wx", "error", "fail"))][:15]:
        print("   ", line)
    for line in fails[:10]:
        print("   ", line)
    print("\n5) 页面文案（定位相关片段）")
    for line in body.splitlines():
        if any(k in line for k in ("定位", "位置", "签到", "课程", "教室")):
            print("   ", line.strip()[:120])
    print("\n[*] 截图 → diagnose.png")


if __name__ == "__main__":
    main()
