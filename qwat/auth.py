"""CAS 自动登录 + 凭据保管。

密码加密完全复刻站点 encrypt.js：

    getAesString(randomString(64) + password, salt, randomString(16))
    key = Utf8(salt)        iv = Utf8(random16)
    AES/CBC/PKCS7 → base64(密文)

即：明文前拼 64 位随机串、IV 随机 16 位，密钥就是页面下发的 pwdEncryptSalt。

凭据在 Windows 上用 DPAPI 加密（只对当前 Windows 账户可解），
非 Windows 退化为 0600 权限的 JSON。
"""
from __future__ import annotations

import base64
import json
import os
import random
import re
import sys
from pathlib import Path

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import cas

CREDS = Path("creds.bin")
FP_FILE = Path("fingerprint.txt")
AES_CHARS = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"


# ---------------------------------------------------------------- 设备指纹
def get_fingerprint(path: Path = FP_FILE) -> str:
    """站点用 MD5 混出设备指纹写进 cookie MULTIFACTOR_BROWSER_FINGERPRINT。

    服务端靠它识别「可信设备」。我们生成一个稳定值并持久化，
    这样每次请求都带同一个指纹，「信任此设备」才能真正落地。
    """
    import hashlib
    if path.exists():
        v = path.read_text("utf-8").strip()
        if v:
            return v
    seed = cas.UA_DESKTOP + "|Windows NT 10.0|zh-CN|1920x1080|24"
    fp = hashlib.md5(seed.encode("utf-8")).hexdigest()
    path.write_text(fp, "utf-8")
    return fp


def attach_fingerprint(session: requests.Session) -> str:
    fp = get_fingerprint()
    session.cookies.set("MULTIFACTOR_BROWSER_FINGERPRINT", fp,
                        domain="authserver.szu.edu.cn", path="/")
    return fp


# ---------------------------------------------------------------- 加解密
def random_string(n: int) -> str:
    return "".join(random.choice(AES_CHARS) for _ in range(n))


def _pkcs7(data: bytes, block: int = 16) -> bytes:
    pad = block - len(data) % block
    return data + bytes([pad]) * pad


def encrypt_password(password: str, salt: str) -> str:
    key = salt.strip().encode("utf-8")
    if len(key) not in (16, 24, 32):
        key = (key + b"\0" * 32)[:32]
    iv = random_string(16).encode("utf-8")
    plain = (random_string(64) + password).encode("utf-8")
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ct = enc.update(_pkcs7(plain)) + enc.finalize()
    return base64.b64encode(ct).decode("ascii")


# ---------------------------------------------------------------- 凭据
def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    import ctypes.wintypes as wt

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    fn = (ctypes.windll.crypt32.CryptProtectData if protect
          else ctypes.windll.crypt32.CryptUnprotectData)
    args = [ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)]
    if not fn(*args):
        raise OSError("DPAPI 调用失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def save_creds(student_id: str, password: str, path: Path = CREDS) -> Path:
    payload = json.dumps({"student_id": student_id, "password": password},
                         ensure_ascii=False).encode("utf-8")
    if sys.platform == "win32":
        blob = b"DPAPI1" + _dpapi(payload, True)
    else:
        blob = b"PLAIN1" + payload
    path.write_bytes(blob)
    if sys.platform != "win32":
        os.chmod(path, 0o600)
    return path


def load_creds(path: Path = CREDS) -> tuple[str, str]:
    if not path.exists():
        cfg = Path("config.yaml")
        if cfg.exists():
            import yaml
            acc = (yaml.safe_load(cfg.read_text("utf-8")) or {}).get("account") or {}
            sid, pwd = str(acc.get("student_id") or ""), str(acc.get("password") or "")
            if sid and pwd:
                return sid, pwd
        raise FileNotFoundError(f"没有凭据文件 {path}，先跑 python scripts/save_creds.py")
    blob = path.read_bytes()
    if blob.startswith(b"DPAPI1"):
        payload = _dpapi(blob[6:], False)
    elif blob.startswith(b"PLAIN1"):
        payload = blob[6:]
    else:
        payload = blob
    data = json.loads(payload.decode("utf-8"))
    return data["student_id"], data["password"]


# ---------------------------------------------------------------- 登录
class LoginError(RuntimeError):
    pass


class NeedCaptcha(LoginError):
    pass


def _field(html: str, name_or_id: str, attr: str = "value") -> str | None:
    m = re.search(rf'<input[^>]*(?:id|name)="{re.escape(name_or_id)}"[^>]*>', html)
    if not m:
        return None
    m2 = re.search(rf'{attr}="([^"]*)"', m.group(0))
    return m2.group(1) if m2 else None


def login(student_id: str, password: str, service: str = cas.SERVICE,
          session: requests.Session | None = None, timeout: int = 25,
          remember: bool = True) -> requests.Session:
    """账号密码登录 CAS，成功后 session 内已带 CASTGC。

    remember=True 会勾上「开启后 7 天内无需再次登录」，拿到长效 TGT；
    否则 CASTGC 是会话级 cookie，服务端一超时就得重登。
    """
    s = session or cas.new_session(cas.UA_DESKTOP)
    attach_fingerprint(s)
    page = s.get(f"{cas.CAS}/login", params={"service": service}, timeout=timeout,
                 allow_redirects=True)
    html = page.text

    salt = _field(html, "pwdEncryptSalt")
    if not salt:
        raise LoginError("登录页没有 pwdEncryptSalt，页面结构可能变了")
    execution = _field(html, "execution") or "e1s1"
    lt = _field(html, "lt") or ""
    switch = (re.search(r'captchaSwitch\s*=\s*"(\d)"', html) or [None, "2"])[1]

    # captchaSwitch=2 时按用户名询问是否要验证码；未知账号会要求，正常学号一般不要求
    need = False
    try:
        r = s.post(f"{cas.CAS}/checkNeedCaptcha.htl",
                   data={"username": student_id}, timeout=timeout)
        need = bool(r.json().get("isNeed"))
    except Exception:
        need = False

    data = {
        "username": student_id,
        "password": encrypt_password(password, salt),
        "lt": lt,
        "execution": execution,
        "_eventId": "submit",
        "cllt": "userNameLogin",
        "dllt": "generalLogin",
        "rmShown": "1",
    }
    if remember:
        data["rememberMe"] = "true"
    if need:
        # 把验证码图片存下来，人工/OCR 兜底
        img = s.get(f"{cas.CAS}/getCaptcha.htl", params={"ts": str(random.random())},
                    timeout=timeout)
        Path("captcha.png").write_bytes(img.content)
        raise NeedCaptcha("本次登录要求图形验证码，已保存 captcha.png（captchaSwitch=%s）" % switch)

    r = s.post(f"{cas.CAS}/login", params={"service": service}, data=data,
               timeout=timeout, allow_redirects=False)

    if r.status_code in (301, 302, 303):
        loc = r.headers.get("Location", "")
        if "ticket=" in loc:
            return s
        raise LoginError(f"登录被重定向到意料之外的位置：{loc[:160]}")

    # 失败时是 401 + 一句中文原因；页面模板里也散落着「验证码」等字样，必须按整句匹配
    text = re.sub(r"<script.*?</script>", " ", r.text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    for msg in ("该账号非常用账号或用户名密码有误", "用户名或密码有误", "密码错误",
                "账号或密码错误", "用户名或密码错误", "账号已被锁定", "密码已过期",
                "验证码错误", "验证码不正确", "认证失败"):
        if msg in text:
            if "验证码" in msg:
                try:
                    img = s.get(f"{cas.CAS}/getCaptcha.htl",
                                params={"ts": str(random.random())}, timeout=timeout)
                    Path("captcha.png").write_bytes(img.content)
                except Exception:
                    pass
                raise NeedCaptcha(f"需要图形验证码：{msg}（已存 captcha.png）")
            raise LoginError(f"登录失败：{msg}")
    raise LoginError(f"登录失败：HTTP {r.status_code}，{len(r.text)} bytes")


def login_and_save(student_id: str, password: str, state: Path = Path("state.json")) -> Path:
    s = login(student_id, password)
    cas.save_cookies(s, state)
    # 立刻验证一次铸票，确保会话真的可用
    cas.mint_ticket(s)
    return state


# ---------------------------------------------------------------- 会话自愈
def ensure_app(state: Path | str = "state.json", creds_path: Path = CREDS) -> requests.Session:
    """拿一个已登录的业务 session：优先复用 state.json，失效则用保存的凭据重登。"""
    state = Path(state)
    if state.exists():
        s = cas.load_state(state)
        try:
            cas.open_app(s)
            return s
        except cas.NotLoggedIn:
            pass
    sid, pwd = load_creds(creds_path)
    print(f"[auth] 会话失效，使用已保存凭据重新登录 {sid} …")
    s = login(sid, pwd)
    cas.save_cookies(s, state)
    cas.open_app(s)
    print("[auth] 重新登录成功，state.json 已更新")
    return s
