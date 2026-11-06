"""课表驱动的自动签到守护进程（真实协议版）。

每 POLL 秒醒一次 → 落在某节课的签到窗口内 → 走 qwat.sign 的完整链路。
窗口外/已签到/不可签 都会安静跳过，不会反复打扰接口。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import yaml

from . import cas, sign

POLL = 60


def in_window(course: dict, t: dt.datetime) -> bool:
    if t.isoweekday() != int(course["weekday"]):
        return False
    start = dt.datetime.combine(t.date(), dt.time.fromisoformat(course["start"]))
    before = dt.timedelta(minutes=int(course.get("window_before", 10)))
    after = dt.timedelta(minutes=int(course.get("window_after", 25)))
    return start - before <= t <= start + after


def load_done(path: Path) -> set[str]:
    return set(json.loads(path.read_text("utf-8"))) if path.exists() else set()


def save_done(path: Path, done: set[str]) -> None:
    path.write_text(json.dumps(sorted(done), ensure_ascii=False), "utf-8")


def attempt(cfg: dict, course: dict, dry_run: bool) -> dict:
    at = None
    if course.get("lat") and course.get("lng"):
        at = (float(course["lat"]), float(course["lng"]))
    return sign.sign(
        state=cfg.get("state_file", "state.json"),
        course_name=course["name"],
        at=at,
        photo=course.get("photo") or cfg.get("photo"),
        dry_run=dry_run,
        jitter_m=float(cfg.get("jitter_m", 0)),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text("utf-8"))
    done_path = Path(cfg.get("done_file", "done.json"))
    done = load_done(done_path)
    print(f"[*] 守护启动 · {len(cfg['courses'])} 节课在册 · 轮询 {POLL}s")

    while True:
        t = dt.datetime.now()
        active = [c for c in cfg["courses"] if in_window(c, t)]
        if not active:
            if a.once:
                print("[*] 当前不在任何课的窗口内")
                return
            time.sleep(POLL)
            continue

        course = active[0]
        key = f"{t.date()}|{course['name']}|{course['start']}"
        if key in done:
            if a.once:
                print(f"[*] {course['name']} 今日已处理")
                return
            time.sleep(POLL)
            continue

        print(f"[{t:%H:%M:%S}] 窗口内：{course['name']}")
        try:
            res = attempt(cfg, course, a.dry_run)
        except cas.NotLoggedIn:
            print("[-] CASTGC 失效：python -m qwat.capture --login")
            if a.once:
                sys.exit(2)
            time.sleep(POLL)
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"[!] {type(exc).__name__}: {exc}")
            if a.once:
                return
            time.sleep(POLL)
            continue

        print(f"    → ok={res.get('ok')} {res.get('reason') or ''}")
        if res.get("ok"):
            done.add(key)
            save_done(done_path, done)

        if a.once:
            return
        time.sleep(POLL)


if __name__ == "__main__":
    main()
