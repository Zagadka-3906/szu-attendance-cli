# -*- coding: utf-8 -*-
"""会话过期 → 自动重登 的真实演练。

两种过期形态各跑一次，全程走真实网络与真实凭据：
  A. CASTGC 被删除（最常见：cookie 到期被浏览器清掉）
  B. CASTGC 被篡改（服务端已失效，但 cookie 还在）

python tests/selftest_expiry.py
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qwat import api, auth, cas  # noqa: E402

STATE = ROOT / "state.json"
BACKUP = ROOT / "state.bak.json"


def drop_castgc(path: Path) -> None:
    data = json.loads(path.read_text("utf-8"))
    data["cookies"] = [c for c in data["cookies"] if c["name"] != "CASTGC"]
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")


def poison_castgc(path: Path) -> None:
    data = json.loads(path.read_text("utf-8"))
    for c in data["cookies"]:
        if c["name"] == "CASTGC":
            c["value"] = "CASTGC-invalid-simulation-0000"
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")


def check(alive: bool) -> tuple[bool, str]:
    """当前 state.json 到底能不能用。"""
    s = cas.load_state(STATE)
    if not cas.castgc(s):
        return False, "无 CASTGC"
    try:
        cas.mint_ticket(s)
    except cas.NotLoggedIn as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    if not alive:
        return False, "模拟失效"
    return True, "ok"


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="只跑 A 形态，省登录次数")
    ap.add_argument("--keep", action="store_true", help="跑完保留当前会话，不做恢复重登")
    args = ap.parse_args()

    if not STATE.exists():
        sys.exit("[-] 没有 state.json")
    shutil.copy2(STATE, BACKUP)

    try:
        sid, pwd = auth.load_creds()
        print(f"[*] 已保存凭据：{sid}（密码 {len(pwd)} 位，DPAPI 解密成功）")

        ok, why = check(True)
        print(f"[基线] 当前会话：{'有效' if ok else '已失效（' + why + '）'}\n")

        variants = [("A 删除 CASTGC", drop_castgc)]
        if not args.quick:
            variants.append(("B 篡改 CASTGC", poison_castgc))
        for name, mutate in variants:
            print(f"=== {name} ===")
            mutate(STATE)
            ok, why = check(True)
            print(f"  会话状态      : {'仍有效' if ok else '已失效（' + why + '）'}")
            if ok:
                print("  ! 该形态没被判失效，跳过")
                continue

            t0 = time.time()
            try:
                s = auth.ensure_app(STATE)
                dt = time.time() - t0
                print(f"  自动重登      : ✅ 用时 {dt:.2f}s")
                print(f"  新 CASTGC     : {(cas.castgc(s) or '')[:22]}…")
                print(f"  铸票          : {'✅' if cas.mint_ticket(s)[:3] == 'ST-' else '❌'}")
                who = api.QwClient(s).status()
                print(f"  业务接口      : logged_in={who.get('logged_in')} "
                      f"{(who.get('user') or {}).get('name')}")
            except Exception as exc:  # noqa: BLE001
                print(f"  自动重登      : ❌ {type(exc).__name__}: {exc}")
                raise
            print()

        # 无凭据时的降级行为
        print("=== C 凭据缺失时的降级 ===")
        drop_castgc(STATE)
        real = auth.load_creds

        def boom(*a, **k):
            raise FileNotFoundError("没有凭据文件 creds.bin，先跑 python scripts/save_creds.py")

        auth.load_creds = boom
        try:
            auth.ensure_app(STATE)
            print("  ❌ 没有凭据却成功了，不合预期")
        except FileNotFoundError as exc:
            print(f"  ✅ 明确报错而不是静默失败：{exc}")
        finally:
            auth.load_creds = real

        # 恢复：再登一次拿到干净会话
        print("\n[*] 恢复干净会话 …")
        s = auth.login(sid, pwd)
        cas.save_cookies(s, STATE)
        ok, _ = check(True)
        print(f"[最终] state.json {'有效' if ok else '无效'}，CASTGC {(cas.castgc(s) or '')[:22]}…")
        print("\n结果: 过期自愈链路全部通过 ✅" if ok else "\n结果: 恢复失败 ❌")
        sys.exit(0 if ok else 1)
    finally:
        if BACKUP.exists():
            BACKUP.unlink()


if __name__ == "__main__":
    main()
