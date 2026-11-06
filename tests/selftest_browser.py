# -*- coding: utf-8 -*-
"""浏览器链路自检：用本地假页面复刻签到弹窗的四段文案，
验证 shim → 重新定位 → 选照片 → 提交 → 成功识别 整条链路。

python tests/selftest_browser.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qwat import checkin  # noqa: E402

HTML = """<!doctype html><html><head><meta charset="utf-8"><title>mock</title></head>
<body>
<h3>确认并拍照签到</h3>
<div id="warn">⚠ 定位服务未就绪，请点击重新获取定位</div>
<button id="recheck">重新获取定位</button>
<div>签到状态 <b id="status">位置未知</b></div>
<input type="file" accept="image/*" capture="camera">
<button id="submit" disabled>上传照片并签到</button>
<script>
  document.getElementById('recheck').onclick = function () {
    navigator.geolocation.getCurrentPosition(function (p) {
      document.getElementById('status').textContent =
        '位置已确认 ' + p.coords.latitude.toFixed(4) + ',' + p.coords.longitude.toFixed(4);
      document.getElementById('warn').remove();
      document.getElementById('submit').disabled = false;
    }, function (e) {
      document.getElementById('status').textContent = '定位服务未就绪';
    }, {timeout: 3000});
  };
  document.getElementById('submit').onclick = function () {
    document.body.innerHTML = '<h1>签到成功</h1>';
  };
</script>
</body></html>"""


def main() -> None:
    import os
    import shutil

    tmpdir = ROOT / "tests" / "_tmp"
    shutil.rmtree(tmpdir, ignore_errors=True)
    tmpdir.mkdir(parents=True, exist_ok=True)
    os.chdir(tmpdir)                      # 截图落在 _tmp，不污染项目根目录

    page_file = tmpdir / "_mock_checkin.html"
    page_file.write_text(HTML, "utf-8")

    photo = tmpdir / "_mock_photo.jpg"
    from PIL import Image
    Image.new("RGB", (720, 1280), (38, 61, 51)).save(photo, quality=85)

    res = checkin.checkin_browser(
        {"lat": 22.5341, "lng": 113.9352, "accuracy": 12, "address": "示例教学楼A-101"},
        state=tmpdir / "_no_state.json",
        photo=str(photo),
        headless=True,
        url=page_file.as_uri(),
    )

    print("结果:", {k: res[k] for k in ("ok", "geo_ok", "clicked")})
    print("尾部文案:", res["tail_text"].strip()[:120])

    ok = res["ok"] and res["geo_ok"] and res["clicked"]["recheck"] and res["clicked"]["submit"]
    print("\n链路:", "全部通过 ✅" if ok else "存在失败项 ❌")
    shutil.rmtree(tmpdir, ignore_errors=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
