"""登录态采集 + 签到接口抓包 + 模板生成。

用法：
  # 1) 首次登录（有头，人工过滑块；登录后自动存 state.json）
  python -m qwat.capture --login

  # 2) 抓一次真实签到，产出 template.json
  python -m qwat.capture --record

  # 3) 校验模板（不真正提交）
  python -m qwat.capture --show

--login  只做认证：企业微信扫码 tab 或 账号密码 tab 均可，成不成看滑块。
--record 打开 /attendance，你在页面上正常点一次「签到」，脚本把全部 XHR 存下来。
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from . import cas

STATE = Path("state.json")
CAPTURED = Path("captured.json")
TEMPLATE = Path("template.json")


def _ensure_browsers() -> None:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("pip install playwright && playwright install chromium") from exc


def login(headless: bool = False, timeout_s: int = 300) -> dict:
    """打开 CAS 登录页，人工完成登录（密码 / 动态码 / 企微扫码三选一）。"""
    _ensure_browsers()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent=cas.UA_DESKTOP,
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
        )
        page = ctx.new_page()
        from urllib.parse import quote
        # type=user 是关键：绕开 service 里 source=user 触发的企微静默授权强制跳转
        url = f"{cas.CAS}/login?service={quote(cas.SERVICE, safe='')}&type=user"
        page.goto(url, wait_until="domcontentloaded")

        print("[*] 浏览器已打开。三种登录方式任选：")
        print("    · 账号登录：学号 + 密码（页面自带 AES 加密，无需自己算）")
        print("    · 验证码登录：学号 + 短信动态码")
        print("    · 企业微信扫码：用手机企业微信扫码（不需要在本机装企微）")
        print(f"[*] 登录成功后会自动跳回 /attendance，等待最长 {timeout_s}s ...")

        deadline = time.time() + timeout_s
        ok = False
        while time.time() < deadline:
            if "qw.szu.edu.cn" in page.url and "/attendance" in page.url:
                ok = True
                break
            page.wait_for_timeout(1000)

        if not ok:
            browser.close()
            raise SystemExit("[-] 超时：未检测到跳回 /attendance，登录未完成")

        page.wait_for_load_state("networkidle")
        ctx.storage_state(path=str(STATE))
        print(f"[+] 登录态已保存 → {STATE.resolve()}")

        # 顺带确认 CASTGC 可用
        s = cas.load_state(STATE)
        print("[+] CASTGC:", (cas.castgc(s) or "缺失")[:24], "...")
        browser.close()
    return json.loads(STATE.read_text("utf-8"))


def record(state: Path = STATE, headless: bool = False, seconds: int = 240) -> list[dict]:
    """在已登录浏览器里抓取一次真实签到产生的全部请求。"""
    _ensure_browsers()
    from playwright.sync_api import sync_playwright

    if not state.exists():
        raise SystemExit("[-] 先跑 --login 生成 state.json")

    hits: list[dict] = []

    def on_request(req):
        if req.resource_type not in ("xhr", "fetch"):
            return
        item = {
            "method": req.method,
            "url": req.url,
            "headers": dict(req.headers),
            "post_data": req.post_data,
            "ts": time.time(),
        }
        hits.append(item)
        body = (req.post_data or "")[:300]
        print(f"  {req.method:6} {req.url[:110]}")
        if body:
            print(f"         body: {body}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=["--no-sandbox"])
        ctx = browser.new_context(storage_state=str(state), user_agent=cas.UA_WXWORK,
                                  locale="zh-CN",
                                  permissions=["geolocation"],
                                  geolocation={"latitude": 22.5333, "longitude": 113.9333})
        page = ctx.new_page()
        page.on("request", on_request)
        page.goto(cas.APP + "/", wait_until="networkidle")
        print(f"[*] 页面已就绪，请在 {seconds}s 内手动完成一次签到 ...")
        page.wait_for_timeout(seconds * 1000)
        ctx.storage_state(path=str(state))
        browser.close()

    CAPTURED.write_text(json.dumps(hits, ensure_ascii=False, indent=2), "utf-8")
    print(f"[+] 抓到 {len(hits)} 条 XHR → {CAPTURED.resolve()}")
    return hits


GEO_KEYS = ("lat", "lng", "lon", "latitude", "longitude", "accuracy", "address",
            "location", "coord", "distance", "sign", "checkin", "qd")


def pick_candidates(hits: list[dict]) -> list[dict]:
    out = []
    for h in hits:
        blob = (h["url"] + (h["post_data"] or "")).lower()
        score = sum(1 for k in GEO_KEYS if k in blob)
        if h["method"] == "POST" and score:
            out.append({**h, "score": score})
    out.sort(key=lambda x: -x["score"])
    return out


def make_template(hit: dict) -> dict:
    """把真实请求反推成可复用的模板：URL 里的数字/ID 换成占位符。"""
    body = hit.get("post_data") or ""
    try:
        payload = json.loads(body)
    except Exception:
        payload = body

    url = hit["url"]
    url_t = re.sub(r"(?<=/)(\d{2,})(?=/|$)", "{taskId}", url)

    def walk(obj):
        if isinstance(obj, dict):
            m = {}
            for k, v in obj.items():
                kl = k.lower()
                if any(g in kl for g in ("lat", "lng", "lon", "acc", "speed", "alt")):
                    m[k] = f"{{{{geo.{k}}}}}"
                elif any(g in kl for g in ("address", "addr", "place")):
                    m[k] = "{{geo.address}}"
                elif any(g in kl for g in ("task", "signid", "attendid", "activityid")):
                    m[k] = f"{{{{task.{k}}}}}"
                else:
                    m[k] = walk(v)
            return m
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        return obj

    return {
        "method": hit["method"],
        "url": url_t,
        "headers": {k: v for k, v in hit["headers"].items()
                    if k.lower() in ("content-type", "accept", "x-requested-with",
                                     "authorization", "token", "referer", "origin")},
        "payload": walk(payload),
        "raw_post": body,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--seconds", type=int, default=240)
    ap.add_argument("--index", type=int, default=0, help="选择第几个候选请求生成模板")
    args = ap.parse_args()

    if args.login:
        login(headless=args.headless)
    elif args.record:
        hits = record(headless=args.headless, seconds=args.seconds)
        cands = pick_candidates(hits)
        if not cands:
            print("[-] 没识别出签到请求，检查 captured.json")
            return
        for i, c in enumerate(cands[:6]):
            print(f"  [{i}] score={c['score']} {c['method']} {c['url'][:100]}")
        tpl = make_template(cands[args.index])
        TEMPLATE.write_text(json.dumps(tpl, ensure_ascii=False, indent=2), "utf-8")
        print(f"[+] 模板 → {TEMPLATE.resolve()}")
        print(json.dumps(tpl, ensure_ascii=False, indent=2)[:1200])
    elif args.show:
        print(TEMPLATE.read_text("utf-8") if TEMPLATE.exists() else "[-] 无模板")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
