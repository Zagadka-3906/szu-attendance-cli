"""分析 CAS 登录页的关键参数（captchaSwitch / saltPassword / 隐藏字段）。

python scripts/cas_inspect.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwat import cas  # noqa: E402


def main() -> None:
    s = cas.new_session(cas.UA_DESKTOP)
    html = cas.probe_login_page(s)
    print(f"页面 {len(html)} bytes\n")

    for pat, label in [
        (r"captchaSwitch\s*=\s*[^;\n]+", "captchaSwitch"),
        (r"<input[^>]*saltPassword[^>]*>", "saltPassword 隐藏域"),
        (r"<input[^>]*name=\"password\"[^>]*>", "password 隐藏域"),
        (r"<input[^>]*pwdEncryptSalt[^>]*>", "加密盐"),
        (r"<input[^>]*id=\"lt\"[^>]*>", "lt"),
        (r"<input[^>]*execution[^>]*>", "execution"),
        (r"var\s+\w*[Cc]aptcha\w*\s*=\s*[^;\n]+", "验证码变量"),
    ]:
        hits = re.findall(pat, html)
        print(f"[{label}] {len(hits)}")
        for h in hits[:3]:
            print("   ", h.strip()[:160])

    print("\n[checkNeedCaptcha 探测]")
    # 换成你自己的学号；不传就用占位值（未注册账号必然 isNeed=true）
    probe_id = sys.argv[1] if len(sys.argv) > 1 else "YOUR_STUDENT_ID"
    for user in (probe_id, "nonexistent_user_probe"):
        r = s.post("https://authserver.szu.edu.cn/authserver/checkNeedCaptcha.htl",
                   data={"username": user}, timeout=15)
        print(f"   {user:<22} {r.status_code} {r.text[:120]}")


if __name__ == "__main__":
    main()
