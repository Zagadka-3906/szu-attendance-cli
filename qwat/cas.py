"""SZU CAS 会话层：铸票 / 登录态采集 / 复用。

实测链路（2026-11 现场抓取，scripts/probe.py 可复现）：

  UA = 桌面 Chrome：
    GET https://authserver.szu.edu.cn/authserver/login?service=...%2Fcas%2Fcallback...
      → 200 统一身份认证页（用户名密码 / 手机动态码 / 企业微信扫码 / 生物识别 四个 tab）

  UA 含 MicroMessenger 或 wxwork：
    同一 URL → 302 /authserver/weixinQYLogin.do?qyId=ww563e8adbd544adf5
            → 302 open.weixin.qq.com/connect/oauth2/authorize?scope=snsapi_base
            → 200 "请在企业微信客户端打开链接"

结论：门禁不是「必须企微客户端」，而是 CAS 按 UA 选登录策略。
      企微 UA 走 snsapi_base 静默授权（在企微容器内免登录，在容器外直接报错）。
      桌面 UA 走标准表单登录 —— 这条路上完全不需要装企业微信。
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import requests

CAS = "https://authserver.szu.edu.cn/authserver"
APP = "https://qw.szu.edu.cn/attendance"
SERVICE = APP + "/cas/callback?redirect=%2Fattendance&source=user"
CALLBACK = APP + "/cas/callback"

UA_WXWORK = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36 wxwork/4.1.26 MicroMessenger/7.0.1 NetType/WIFI Language/zh"
)
UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36"
)


class CasError(RuntimeError):
    pass


class NotLoggedIn(CasError):
    """CASTGC 缺失或已过期，需要重新走一次登录。"""


def new_session(ua: str = UA_DESKTOP) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": ua,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    })
    return s


def load_state(path: str | Path) -> requests.Session:
    """从 Playwright storage_state 还原 requests.Session。"""
    data = json.loads(Path(path).read_text("utf-8"))
    s = new_session()
    for c in data.get("cookies", []):
        s.cookies.set(
            c["name"], c["value"],
            domain=c.get("domain") or "",
            path=c.get("path") or "/",
        )
    return s


def castgc(session: requests.Session) -> str | None:
    for c in session.cookies:
        if c.name == "CASTGC":
            return c.value
    return None


def mint_ticket(session: requests.Session, service: str = SERVICE) -> str:
    """用已有 CASTGC 换一张 ST 票据（CASTGC 有效期内可无限次铸票）。"""
    r = session.get(f"{CAS}/login", params={"service": service},
                    allow_redirects=False, timeout=20)
    loc = r.headers.get("Location", "")
    if r.status_code == 200:
        raise NotLoggedIn("CAS 返回登录页：CASTGC 已失效，需要重新登录")
    if "weixinQYLogin" in loc:
        raise NotLoggedIn("CAS 跳向 weixinQYLogin.do：无有效 CASTGC")
    qs = parse_qs(urlparse(loc).query)
    if "ticket" not in qs:
        raise CasError(f"未取到 ticket：{r.status_code} {loc[:200]}")
    return qs["ticket"][0]


def open_app(session: requests.Session) -> requests.Response:
    """铸票 → 回灌 /cas/callback → 落地 /attendance（写入业务 Cookie）。"""
    ticket = mint_ticket(session)
    session.get(CALLBACK, params={"redirect": "/attendance", "source": "user",
                                  "ticket": ticket}, allow_redirects=True, timeout=20)
    return session.get(APP + "/", allow_redirects=True, timeout=20)


def probe_login_page(session: requests.Session) -> str:
    """拿 CAS 登录页 HTML（加 type=user 绕开 source=user 的企微强制跳转）。"""
    url = f"{CAS}/login?service={quote(SERVICE, safe='')}&type=user"
    return session.get(url, timeout=20).text


def save_cookies(session: requests.Session, path: str | Path) -> None:
    cookies = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
               for c in session.cookies]
    Path(path).write_text(json.dumps({"cookies": cookies}, ensure_ascii=False, indent=2), "utf-8")
