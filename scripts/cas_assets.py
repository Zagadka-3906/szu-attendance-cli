"""把 CAS 登录页及其全部 JS 拉下来，供逆向加密逻辑用。

python scripts/cas_assets.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwat import cas  # noqa: E402

OUT = Path("dist/cas")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    s = cas.new_session(cas.UA_DESKTOP)
    html = cas.probe_login_page(s)
    (OUT / "login.html").write_text(html, "utf-8")
    print(f"[+] login.html {len(html)} bytes")

    assets = set(re.findall(r'(?:src|href)="([^"]+\.(?:js|css))', html))
    print(f"[+] {len(assets)} 个资源")
    for a in sorted(assets):
        url = urljoin("https://authserver.szu.edu.cn/authserver/login", a)
        try:
            r = s.get(url, timeout=30)
        except Exception as exc:  # noqa: BLE001
            print(f"    ! {a}: {exc}")
            continue
        name = re.sub(r"[^\w.\-]", "_", a.rsplit("/", 1)[-1])[:70]
        (OUT / name).write_text(r.text, "utf-8")
        print(f"    {r.status_code} {len(r.text):>7}  {name}")

    # 找出与密码加密相关的片段
    print("\n=== 加密相关命中 ===")
    for f in sorted(OUT.glob("*.js")):
        body = f.read_text("utf-8", errors="ignore")
        if any(k in body for k in ("pwdEncryptSalt", "encrypt", "CryptoJS", "AES")):
            print(f"--- {f.name} ({len(body)} bytes)")
            for kw in ("pwdEncryptSalt", "encryptPassword", "CryptoJS.AES", "AES.encrypt"):
                for m in re.finditer(kw, body):
                    seg = body[max(0, m.start() - 200): m.start() + 400]
                    print(f"  [{kw}] …{seg.strip()[:520]}…")
                    break


if __name__ == "__main__":
    main()
