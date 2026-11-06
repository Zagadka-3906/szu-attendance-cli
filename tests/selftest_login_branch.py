# -*- coding: utf-8 -*-
"""登录分支自检：用一个不存在的账号验证「验证码分支」会被正确识别。

不会触及任何真实账号的失败计数。
python tests/selftest_login_branch.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qwat import auth  # noqa: E402


def main() -> None:
    try:
        auth.login("zz_no_such_user_9527", "whatever")
    except auth.NeedCaptcha as exc:
        print("✅ 正确识别为需要验证码：", exc)
        print("   captcha.png:", Path("captcha.png").exists())
        return
    except auth.LoginError as exc:
        print("✅ 走了正常登录并被拒绝：", exc)
        return
    print("❌ 既没抛 NeedCaptcha 也没抛 LoginError —— 分支判断有问题")


if __name__ == "__main__":
    main()
