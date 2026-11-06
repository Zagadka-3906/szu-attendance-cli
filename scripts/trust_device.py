"""一次性完成 CAS 多因子认证，并把服务器登记为「可信设备」。

背景：新 IP 首次登录会被 `reAuthCheck/reAuthLoginView.do?isMultifactor=true` 拦住，
要求企业微信/短信验证码。只要在提交时带 `skipTmpReAuth=true`（页面上的「信任此设备」），
服务端会记住这台设备，之后同 IP 的密码登录不再要验证码。

用法（两步，中间保留同一个会话）：
  python scripts/trust_device.py --send            # 触发验证码（默认发到企业微信）
  python scripts/trust_device.py --code 123456     # 提交并信任设备
  python scripts/trust_device.py --status          # 看当前是否已可信
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwat import auth, cas  # noqa: E402

PENDING = Path(".reauth.json")
CODE_TYPE = {
    "3": "reAuthDynamicCodeType",        # 短信
    "4": "reAuthWChatDynamicCodeType",   # 企业微信
    "5": "reAuthCpdailyDynamicCodeType",
    "11": "reAuthEmailDynamicCodeType",
    "12": "reAuthDingTalkDynamicCodeType",
    "13": "reAuthWeLinkDynamicCodeType",
    "15": "reAuthWeChatServiceDynamicCodeType",
}


def _params(html: str) -> dict:
    m = re.search(r"reAuthParams\s*=\s*\{.*?\n\s*\}\s*;", html, re.S)
    if not m:
        return {}
    obj = m.group(0).rstrip().rstrip(";")
    try:
        return {k: str(v) for k, v in json.loads(obj).items()}
    except Exception:  # noqa: BLE001
        raw = dict(re.findall(r'"([A-Za-z_]\w*)"\s*:\s*"((?:[^"\\]|\\.)*)"', obj))
        out = {}
        for k, v in raw.items():
            try:
                out[k] = json.loads(f'"{v}"')       # 正确反转义 \/ 与 \u0026
            except Exception:  # noqa: BLE001
                out[k] = v.replace("\\/", "/").replace("\\u0026", "&")
        return out


def _save_session(s, params) -> None:
    PENDING.write_text(json.dumps({
        "cookies": [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                    for c in s.cookies],
        "params": params,
    }, ensure_ascii=False), "utf-8")


def _load_session():
    data = json.loads(PENDING.read_text("utf-8"))
    s = cas.new_session(cas.UA_DESKTOP)
    for c in data["cookies"]:
        s.cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
    return s, data["params"]


def _reach_reauth(s, service=cas.SERVICE) -> tuple[dict, object]:
    sid, pwd = auth.load_creds()
    fp = auth.attach_fingerprint(s)          # 关键：带上设备指纹
    print(f"[*] 指纹 {fp[:16]}…")
    page = s.get(f"{cas.CAS}/login", params={"service": service}, timeout=25)
    html = page.text
    salt = re.search(r'id="pwdEncryptSalt"[^>]*value="([^"]+)"', html).group(1)
    execution = re.search(r'name="execution"[^>]*value="([^"]+)"', html)
    execution = execution.group(1) if execution else "e1s1"
    lt = re.search(r'name="lt"[^>]*value="([^"]*)"', html)
    lt = lt.group(1) if lt else ""
    data = {
        "username": sid, "password": auth.encrypt_password(pwd, salt),
        "lt": lt, "execution": execution, "_eventId": "submit",
        "cllt": "userNameLogin", "dllt": "generalLogin",
        "rmShown": "1", "rememberMe": "true",
    }
    r = s.post(f"{cas.CAS}/login", params={"service": service}, data=data,
               timeout=25, allow_redirects=True)
    return _params(r.text), r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="触发验证码")
    ap.add_argument("--code", help="提交验证码并信任设备")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--retry", action="store_true", help="认证成功后重试换票")
    ap.add_argument("--type", default=None, help="切换验证方式：3短信 / 4企业微信 / 11邮箱")
    ap.add_argument("--state", default="state.json")
    a = ap.parse_args()

    if a.send:
        s = cas.new_session(cas.UA_DESKTOP)
        params, r = _reach_reauth(s)
        if not params:
            print(f"[+] 没有触发二次认证，直接登录成功（{r.status_code}）")
            cas.save_cookies(s, Path(a.state))
            print("[+] 会话已保存，无需验证码")
            return
        print(f"[*] 需要二次认证：reAuthType={params.get('reAuthType')} "
              f"isMultifactor={params.get('isMultifactor')}")

        if a.type and a.type != params.get("reAuthType"):
            s.post(f"{cas.CAS}/reAuthCheck/changeReAuthType.do",
                   data={"isMultifactor": params.get("isMultifactor"),
                         "reAuthType": a.type, "service": params.get("service")},
                   timeout=20)
            _, r = _reach_reauth(s)
            params = _params(r.text)
            print(f"[*] 已切换为 reAuthType={params.get('reAuthType')}")

        ctype = CODE_TYPE.get(str(params.get("reAuthType")))
        if not ctype:
            print(f"[-] 不认识的验证方式 {params.get('reAuthType')}，可选：{list(CODE_TYPE)}")
            sys.exit(2)

        resp = s.post(f"{cas.CAS}/dynamicCode/getDynamicCodeByReauth.do",
                      data={"userName": params.get("reAuthUserId"), "authCodeTypeName": ctype},
                      timeout=20)
        try:
            j = resp.json()
        except Exception:
            j = {"raw": resp.text[:200]}
        print(f"[*] 发送结果: {json.dumps(j, ensure_ascii=False)[:200]}")
        _save_session(s, params)
        print(f"[+] 会话已暂存 {PENDING}，拿到验证码后执行：")
        print(f"    python scripts/trust_device.py --code XXXXXX")

    elif a.code:
        s, params = _load_session()
        data = {
            "service": cas.SERVICE,          # 用规范值，避免 JSON 转义残留
            "reAuthType": params.get("reAuthType"),
            "isMultifactor": params.get("isMultifactor"),
            "dynamicCode": a.code.strip(),
            "password": "",
            "uuid": "",
            "answer1": "",
            "answer2": "",
            "otpCode": "",
            "skipTmpReAuth": "true",          # 关键：信任此设备
        }
        print(f"[*] 提交 service={data['service'][:70]}… reAuthType={data['reAuthType']}")
        r = s.post(f"{cas.CAS}/reAuthCheck/reAuthSubmit.do", data=data, timeout=25)
        print(f"[*] HTTP {r.status_code}")
        try:
            j = r.json()
            print(f"[*] 完整响应: {json.dumps(j, ensure_ascii=False)[:600]}")
        except Exception:
            print(f"[-] 非 JSON 响应: {r.text[:300]}")
            sys.exit(1)
        if j.get("code") in ("reAuth_failed", "reAuth_unauthorized"):
            print(f"[-] 失败：{j.get('msg')}")
            sys.exit(1)

        # 认证通过 → 重新走一次 login 拿票据
        t = s.get(f"{cas.CAS}/login", params={"service": cas.SERVICE},
                  timeout=25, allow_redirects=False)
        loc = t.headers.get("Location", "")
        print(f"[*] 回到 login → {t.status_code} {loc[:110]}")
        s.get(f"{cas.CAS}/login", params={"service": cas.SERVICE}, timeout=25, allow_redirects=True)
        cas.save_cookies(s, Path(a.state))
        try:
            ticket = cas.mint_ticket(s)
            print(f"[+] 铸票成功 {ticket[:24]}…")
            print(f"[+] 会话已保存 → {Path(a.state).resolve()}")
            PENDING.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[-] 铸票失败：{exc}")
            sys.exit(1)

    elif a.retry:
        import time
        s, params = _load_session()
        for i in range(4):
            r = s.get(f"{cas.CAS}/login", params={"service": cas.SERVICE},
                      timeout=25, allow_redirects=False)
            loc = r.headers.get("Location", "")
            print(f"[{i}] {r.status_code} → {loc[:130]}")
            if "reAuthLoginView" not in loc:
                if "ticket=" in loc:
                    s.get(loc, timeout=25, allow_redirects=True)
                    cas.save_cookies(s, Path(a.state))
                    print("[+] 会话已保存 →", Path(a.state).resolve())
                    print("[+] 铸票", cas.mint_ticket(s)[:24], "…")
                    PENDING.unlink(missing_ok=True)
                    return
            time.sleep(3)
        print("[-] 仍在二次认证：需要重新发码")
        sys.exit(1)

    elif a.status:
        s = cas.load_state(Path(a.state))
        try:
            print("[+] 会话可用，铸票", cas.mint_ticket(s)[:22], "…")
        except Exception as exc:  # noqa: BLE001
            print(f"[-] {exc}")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
