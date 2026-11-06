"""探查 CAS 二次认证（reAuth / Multifactor）到底要什么。

python scripts/probe_reauth.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwat import auth, cas  # noqa: E402


def clean(html: str) -> str:
    t = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def main() -> None:
    sid, pwd = auth.load_creds()
    s = cas.new_session(cas.UA_DESKTOP)

    page = s.get(f"{cas.CAS}/login", params={"service": cas.SERVICE}, timeout=25)
    html = page.text
    salt = re.search(r'id="pwdEncryptSalt"[^>]*value="([^"]+)"', html).group(1)
    execution = re.search(r'name="execution"[^>]*value="([^"]+)"', html)
    execution = execution.group(1) if execution else "e1s1"
    lt = re.search(r'name="lt"[^>]*value="([^"]*)"', html)
    lt = lt.group(1) if lt else ""

    data = {
        "username": sid,
        "password": auth.encrypt_password(pwd, salt),
        "lt": lt, "execution": execution, "_eventId": "submit",
        "cllt": "userNameLogin", "dllt": "generalLogin",
        "rmShown": "1", "rememberMe": "true",
    }
    r = s.post(f"{cas.CAS}/login", params={"service": cas.SERVICE}, data=data,
               timeout=25, allow_redirects=True)
    print("最终 URL:", r.url[:220])
    print("HTTP:", r.status_code, "| 长度:", len(r.text))
    print("COOKIES:", [(c.name, c.domain) for c in s.cookies])
    print("\n页面文案:")
    print(" ", clean(r.text)[:1200])

    for kw in ("短信", "验证码", "二次", "二次认证", "多因子", "确认", "企微", "微信",
               "reAuth", "multifactor", "MFA", "手机"):
        if kw in r.text:
            print(f"  · 出现关键词: {kw}")

    (Path("dist") / "reauth.html").parent.mkdir(exist_ok=True)
    (Path("dist") / "reauth.html").write_text(r.text, "utf-8")


if __name__ == "__main__":
    main()
