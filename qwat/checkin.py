"""签到执行器。

两条路径：
  A. HTTP 直连（config.mode=http）——服务端只看到提交的经纬度。
  B. 浏览器驱动（config.mode=browser）——页面强依赖企微 JSSDK / 拍照上传时用这条。
     wxshim.js 会在页面脚本之前接管 navigator.geolocation / wx.getLocation /
     WeixinJSBridge / AMap / qq.maps 五条取点通道，所以「定位服务未就绪」不再出现。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

from . import cas, geo

TEMPLATE = Path("template.json")
SHIM_PATH = Path(__file__).with_name("wxshim.js")


# ---------------------------------------------------------------- 模板渲染
def _render(obj, ctx: dict):
    if isinstance(obj, dict):
        return {k: _render(v, ctx) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_render(v, ctx) for v in obj]
    if isinstance(obj, str):
        def repl(m):
            ns, key = m.group(1).split(".", 1)
            return str(ctx.get(ns, {}).get(key, m.group(0)))
        return re.sub(r"\{\{([a-zA-Z_]+\.\w+)\}\}", repl, obj)
    return obj


def render_template(tpl: dict, coords: dict, task: dict | None = None) -> dict:
    ctx = {"geo": coords, "task": task or {}}
    return {
        "method": tpl["method"],
        "url": _render(tpl["url"], ctx),
        "headers": _render(tpl["headers"], ctx),
        "payload": _render(tpl["payload"], ctx),
    }


# ---------------------------------------------------------------- A. HTTP
def checkin_http(session: requests.Session, coords: dict, task: dict | None = None,
                 tpl_path: Path = TEMPLATE, dry_run: bool = False) -> dict:
    tpl = json.loads(tpl_path.read_text("utf-8"))
    req = render_template(tpl, coords, task)

    headers = dict(req["headers"])
    headers.setdefault("Referer", cas.APP + "/")
    headers.setdefault("Origin", "https://qw.szu.edu.cn")

    payload = req["payload"]
    if dry_run:
        print("[dry-run]", req["method"], req["url"])
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return {"dry_run": True}

    if isinstance(payload, (dict, list)):
        r = session.request(req["method"], req["url"], json=payload,
                            headers=headers, timeout=20)
    else:
        r = session.request(req["method"], req["url"], data=payload,
                            headers=headers, timeout=20)

    print(f"[{r.status_code}] {req['url'][:110]}")
    print("   ", r.text[:400])
    return {"status": r.status_code, "body": r.text[:2000],
            "ok": r.ok and '"code":0' in r.text.replace(" ", "")}


# ---------------------------------------------------------------- B. 浏览器
def _shim_js(coords: dict) -> str:
    return (SHIM_PATH.read_text("utf-8")
            .replace("__LAT__", repr(float(coords["lat"])))
            .replace("__LNG__", repr(float(coords["lng"])))
            .replace("__ACC__", repr(float(coords.get("accuracy", 12))))
            .replace("__ADDRESS__", coords.get("address") or "广东省深圳市南山区"))


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
                    keep_open: bool = False) -> dict:
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

        if photo:
            try:
                page.set_input_files("input[type=file]", photo, timeout=4000)
                page.wait_for_timeout(1500)
            except Exception as exc:
                logs.append(f"photo: {exc}")

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
        "geo_ok": geo_ok,
        "clicked": {"recheck": clicked, "submit": submit},
        "posts": posts,
        "screenshots": ["step1_landing.png", "step2_geo.png", "step3_result.png"],
        "tail_text": after[-600:],
    }


# ---------------------------------------------------------------- 入口
def run(state: Path, coords: dict, task: dict | None = None, mode: str = "http",
        dry_run: bool = False, jitter_m: float = 4.0, photo: str | None = None,
        headless: bool = True, keep_open: bool = False) -> dict:
    lng, lat = geo.jitter(coords["lng"], coords["lat"], jitter_m)
    c = {"lng": round(lng, 6), "lat": round(lat, 6),
         "accuracy": coords.get("accuracy", 12),
         "address": coords.get("address", "")}

    if mode == "browser":
        return checkin_browser(c, state=state, photo=photo,
                               headless=headless, keep_open=keep_open)

    s = cas.load_state(state)
    try:
        cas.open_app(s)
    except cas.NotLoggedIn:
        return {"ok": False, "reason": "CASTGC 失效，需要重新 --login"}
    return checkin_http(s, c, task, dry_run=dry_run)


def _cli() -> None:
    import argparse
    import yaml

    ap = argparse.ArgumentParser(description="手动触发一次签到")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--course", default=None, help="课名，缺省取课表第一节")
    ap.add_argument("--coords", default=None, help='直接给坐标 {"lat":22.53,"lng":113.93}')
    ap.add_argument("--state", default="state.json")
    ap.add_argument("--mode", default=None, choices=["http", "browser"])
    ap.add_argument("--photo", default=None, help="拍照签到时使用的图片路径")
    ap.add_argument("--head", action="store_true", help="有头模式，便于肉眼确认")
    ap.add_argument("--keep-open", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    mode = a.mode or "http"
    if a.coords:
        coords = json.loads(a.coords)
    else:
        cfg = yaml.safe_load(Path(a.config).read_text("utf-8"))
        courses = cfg["courses"]
        c = next((x for x in courses if x["name"] == a.course), courses[0])
        coords = {"lat": c["lat"], "lng": c["lng"],
                  "accuracy": c.get("accuracy", 12), "address": c.get("room", "")}
        mode = a.mode or cfg.get("mode", "http")

    res = run(Path(a.state), coords, mode=mode, dry_run=a.dry_run,
              photo=a.photo, headless=not a.head, keep_open=a.keep_open)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()
