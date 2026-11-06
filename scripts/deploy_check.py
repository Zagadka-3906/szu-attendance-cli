"""检查当前机器的会话、楼栋、排程与照片；不会上传照片或提交签到。"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwat import api, autoplan  # noqa: E402


def check(state: Path, plan_path: Path, photo: Path) -> list[str]:
    failures: list[str] = []
    offset = dt.datetime.now().astimezone().utcoffset()
    print(f"[时区] UTC{offset}")
    if offset != dt.timedelta(hours=8):
        failures.append("运行时区必须是 UTC+8")

    try:
        client = api.QwClient.from_state(state)
        if not client.status().get("logged_in"):
            failures.append("业务会话无效，请重新登录")
        else:
            print("[会话] 有效")
            buildings = client.buildings()
            print(f"[楼栋] {len(buildings)} 条")
            if not buildings:
                failures.append("楼栋接口没有返回可用数据")
    except Exception as exc:
        failures.append(f"会话或网络检查失败：{type(exc).__name__}: {exc}")

    try:
        plan = autoplan.load_plan(plan_path)
        now = dt.datetime.now()
        pending = [e for e in plan if e.get("state") == "pending"
                   and dt.datetime.fromisoformat(f"{e['date']}T{e['end']}") >= now]
        print(f"[排程] {len(plan)} 条，尚可执行 {len(pending)} 条")
        if not pending:
            failures.append("没有可执行排程，请先 --build 或 --rebuild 并检查课表")
        if any(not Path(e.get("photo") or photo).is_file() for e in pending):
            failures.append("待执行任务缺少照片，请准备 JPEG 并用 --photo 指定")
    except (ValueError, TypeError, KeyError, OSError) as exc:
        failures.append(f"排程不可用：{exc}")
    return failures


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", type=Path, default=Path("state.json"))
    ap.add_argument("--plan", type=Path, default=Path("plan.json"))
    ap.add_argument("--photo", type=Path, default=Path("photo.jpg"))
    args = ap.parse_args()
    failures = check(args.state, args.plan, args.photo)
    if failures:
        for failure in failures:
            print(f"[-] {failure}")
        raise SystemExit(1)
    print("[+] 检查通过：当前会话、排程和照片可用；最终签到结果仍由服务端决定")


if __name__ == "__main__":
    main()
