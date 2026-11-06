# -*- coding: utf-8 -*-
"""定位 shim 自检：不依赖任何站点，纯本地页面验证四层接管是否生效。

python tests/selftest_shim.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LAT, LNG, ACC = 22.534100, 113.935200, 12
SHIM = (ROOT / "qwat" / "wxshim.js").read_text("utf-8") \
    .replace("__LAT__", str(LAT)).replace("__LNG__", str(LNG)) \
    .replace("__ACC__", str(ACC)).replace("__ADDRESS__", "广东省深圳市南山区")

HTML = """<!doctype html><html><head><meta charset="utf-8"><title>shim test</title></head>
<body><script>
window.__RESULT__ = {steps: []};
function done(k, v){ window.__RESULT__.steps.push([k, v]); }
navigator.geolocation.getCurrentPosition(
  p => done('navigator', {lat: p.coords.latitude, lng: p.coords.longitude, acc: p.coords.accuracy}),
  e => done('navigator', {error: e.code}));
if (window.wx && window.wx.getLocation) {
  wx.getLocation({type: 'gcj02',
    success: r => done('wx', {lat: r.latitude, lng: r.longitude, type: r.type}),
    fail: e => done('wx', {error: e.errMsg})});
} else { done('wx', {error: 'missing'}); }
if (window.wx && window.wx.config) {
  wx.config({jsApiList: ['getLocation'],
    success: () => done('config', 'ok'),
    fail: e => done('config', e)});
}
navigator.permissions.query({name: 'geolocation'}).then(
  p => done('perm', p.state));
</script></body></html>"""


def main() -> None:
    from playwright.sync_api import sync_playwright

    tmp = ROOT / "tests" / "_shim_probe.html"
    tmp.write_text(HTML, "utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_context().new_page()
        page.add_init_script(SHIM)
        page.goto(tmp.as_uri())
        page.wait_for_timeout(800)
        res = page.evaluate("window.__RESULT__")
        ver = page.evaluate("window.__QW_GEO_SHIM__")
        browser.close()

    tmp.unlink(missing_ok=True)
    got = dict((k, v) for k, v in res["steps"])
    print("shim 标记 :", json.dumps(ver, ensure_ascii=False))
    print("四层结果  :")
    for k, v in got.items():
        print(f"  {k:10} {json.dumps(v, ensure_ascii=False)}")

    ok = True
    nav = got.get("navigator") or {}
    if abs(nav.get("lat", 0) - LAT) > 1e-6 or abs(nav.get("lng", 0) - LNG) > 1e-6:
        print("  [FAIL] navigator.geolocation 未接管"); ok = False
    wx = got.get("wx") or {}
    if abs(wx.get("lat", 0) - LAT) > 1e-6:
        print("  [FAIL] wx.getLocation 未接管"); ok = False
    if got.get("perm") != "granted":
        print("  [FAIL] permissions.query 未接管"); ok = False
    if got.get("config") != "ok":
        print("  [FAIL] wx.config 未接管"); ok = False

    print("\n结果:", "全部通过 ✅" if ok else "存在失败项 ❌")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
