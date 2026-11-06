"""复现现场侦察：证明门禁是「UA 驱动的登录策略选择」，不是企微客户端强校验。

python scripts/probe.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwat import cas  # noqa: E402

UAS = {
    "desktop Chrome": cas.UA_DESKTOP,
    "wxwork 4.1.26": cas.UA_WXWORK,
    "MicroMessenger": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                       "AppleWebKit/605.1.15 MicroMessenger/8.0.44"),
}

print("[1] 未登录直连应用")
s = cas.new_session()
r = s.get(cas.APP, allow_redirects=False, timeout=20)
print("    ", r.status_code, "->", r.headers.get("Location", "")[:120])

print("\n[2] UA 矩阵：同一 URL，不同 UA")
for name, ua in UAS.items():
    sess = cas.new_session(ua)
    rr = sess.get(f"{cas.CAS}/login", params={"service": cas.SERVICE},
                  allow_redirects=False, timeout=20)
    loc = rr.headers.get("Location", "")
    verdict = "企微静默授权(snsapi_base)" if "weixinQYLogin" in loc else "正常登录页"
    print(f"     {name:16} {rr.status_code}  {verdict}")

print("\n[3] 桌面 UA 下登录页的能力清单")
html = cas.probe_login_page(cas.new_session(cas.UA_DESKTOP))
for label, needle in [
    ("用户名密码登录", "pwdLoginDiv"),
    ("手机动态码登录", "dynamicLogin"),
    ("企业微信扫码登录", "qrLogin"),
    ("生物识别登录", "fidoLogin"),
    ("密码前端加密盐", "pwdEncryptSalt"),
]:
    print(f"     {label:14} {'YES' if needle in html else 'no'}")
print(f"     HTML {len(html)} bytes")

print("\n[4] 企微 OAuth 的 corp id")
r = cas.new_session(cas.UA_WXWORK).get(
    f"{cas.CAS}/weixinQYLogin.do",
    params={"qyId": "ww563e8adbd544adf5", "success": f"{cas.CAS}/login"},
    allow_redirects=False, timeout=20)
print("    ", r.headers.get("Location", "")[:170])

print("\n[5] 应用侧未鉴权探测")
for p in ("/attendance/api/v1", "/attendance/v3/api-docs", "/attendance/actuator/health"):
    rr = cas.new_session().get("https://qw.szu.edu.cn" + p, allow_redirects=False, timeout=15)
    print(f"     {p:32} {rr.status_code}")
