# -*- coding: utf-8 -*-
"""加密等价性自检：用站点自己的 encrypt.js 双向验证我们的实现，不碰服务器。

  A. 站点加密 → 站点解密 == 前缀+密码，且 Python 用同一组 key/iv 也解得出
  B. 我们的密文 → 站点 CryptoJS 解出 == 我们的前缀+密码

python tests/selftest_encrypt.py
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

from qwat import auth, cas  # noqa: E402

PASSWORD = "Pa55w0rd!ceshi#2026"
JS = """([pwd, ourCipher, ourIv, ourSalt]) => {
    const salt = document.getElementById('pwdEncryptSalt').value;
    const rec = [];
    const orig = window.randomString;
    window.randomString = function(n){ const v = orig(n); rec.push([n, v]); return v; };
    const out = window.encryptPassword(pwd, salt);
    window.randomString = orig;

    const key  = CryptoJS.enc.Utf8.parse(salt);
    const last = rec[rec.length - 1][1];
    const dec = CryptoJS.AES.decrypt({ciphertext: CryptoJS.enc.Base64.parse(out)}, key,
                {iv: CryptoJS.enc.Utf8.parse(last), mode: CryptoJS.mode.CBC, padding: CryptoJS.pad.Pkcs7});

    const ok2 = CryptoJS.AES.decrypt({ciphertext: CryptoJS.enc.Base64.parse(ourCipher)},
                CryptoJS.enc.Utf8.parse(ourSalt),
                {iv: CryptoJS.enc.Utf8.parse(ourIv), mode: CryptoJS.mode.CBC, padding: CryptoJS.pad.Pkcs7});

    return {salt, rec, out, decInPage: dec.toString(CryptoJS.enc.Utf8),
            ourPlainInPage: ok2.toString(CryptoJS.enc.Utf8)};
}"""


def py_decrypt(b64: str, salt: str, iv: bytes) -> str:
    raw = base64.b64decode(b64)
    dec = Cipher(algorithms.AES(salt.strip().encode()), modes.CBC(iv)).decryptor()
    plain = dec.update(raw) + dec.finalize()
    return plain[:-plain[-1]].decode("utf-8")


def main() -> None:
    # 拦截 random_string 拿到我们这次的 iv 与前缀
    captured: dict[int, str] = {}
    real = auth.random_string
    auth.random_string = lambda n: captured.setdefault(n, real(n)) or captured[n]
    try:
        salt = "0123456789abcdef"
        our_cipher = auth.encrypt_password(PASSWORD, salt)
    finally:
        auth.random_string = real
    our_prefix, our_iv = captured[64], captured[16]

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox"])
        pg = b.new_context().new_page()
        pg.goto(f"{cas.CAS}/login?service={cas.SERVICE}&type=user", wait_until="domcontentloaded")
        r = pg.evaluate(JS, [PASSWORD, our_cipher, our_iv, salt])
        b.close()

    prefix, iv = r["rec"][0][1], r["rec"][-1][1]
    ours_from_site = py_decrypt(r["out"], r["salt"], iv.encode())

    ok_a1 = r["decInPage"] == prefix + PASSWORD
    ok_a2 = ours_from_site == prefix + PASSWORD
    ok_b = r["ourPlainInPage"] == our_prefix + PASSWORD

    print(f"salt            {r['salt']}")
    print(f"A1 站点自解      {'✅' if ok_a1 else '❌'}  ({len(r['decInPage'])} 字符)")
    print(f"A2 Python 解站点 {'✅' if ok_a2 else '❌'}  ({len(ours_from_site)} 字符)")
    print(f"B  站点解我们的  {'✅' if ok_b else '❌'}  ({len(r['ourPlainInPage'])} 字符)")
    print("\n结果:", "实现与站点完全等价 ✅" if ok_a1 and ok_a2 and ok_b else "不等价 ❌")
    sys.exit(0 if ok_a1 and ok_a2 and ok_b else 1)


if __name__ == "__main__":
    main()
