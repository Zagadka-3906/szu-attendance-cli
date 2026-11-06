"""查看 reAuth 页面里的 reAuthParams 结构。

python scripts/inspect_reauth_html.py [dist/reauth.html]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> None:
    p = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/reauth.html")
    if not p.exists():
        sys.exit(f"没有 {p}")
    s = p.read_text("utf-8", errors="replace")

    m = re.search(r"reAuthParams\s*=\s*\{.*?\n\s*\}\s*;", s, re.S)
    obj = m.group(0) if m else ""
    print(f"=== reAuthParams（{len(obj)} 字符）===")
    for k, v in re.findall(r'"([A-Za-z_]\w*)"\s*:\s*"([^"]*)"', obj):
        if k.lower().startswith("reauthdec"):
            continue
        print(f"  {k:20} = {v[:90]}")
    if not obj:
        print("未找到")

    print("\n=== 关键字段 ===")
    for k in ("reAuthType", "isMultifactor", "isSleepAccount", "service",
              "pwdEncryptSalt", "showPhoneOrEmail"):
        for mm in re.finditer(rf"{k}\s*[:=]\s*[\"']?([^\"',;\n}}]+)", s):
            print(f"  {k:18} = {mm.group(1)[:80]}")
            break

    print("\n=== 可选验证方式 ===")
    for mm in re.finditer(r"reAuthByCombined\('(\w+)'\)", s):
        print("  combined:", mm.group(1))


if __name__ == "__main__":
    main()
