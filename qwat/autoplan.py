"""全课表自动排程：课前 1–5 分钟随机时刻，围栏内随机坐标，到点自动签到。

  python -m qwat.autoplan --build                  # 生成 plan.json 并打印排程表
  python -m qwat.autoplan --run                    # 常驻，按排程执行
  python -m qwat.autoplan --run --once             # 只处理当前到点的条目
  python -m qwat.autoplan --run --dry-run          # 走完整链路但不提交

设计要点：
  · 每条课次的时间与坐标在 build 阶段就随机定好并落盘，重启不会变（可 --rebuild 重摇）
  · 坐标取楼栋围栏圆心 + 随机方位/距离（25m ~ 0.85R），保证 in_range 且不总是同一点
  · 课堂开始前 15 分钟服务端就放行（实测 valid_start = start - 15min），前 1–5 分钟必在窗口内
  · 已签到的课次由 check-signin-status 直接短路，不会重复提交
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import time
from pathlib import Path

from . import api, cas, geo

PLAN = Path("plan.json")


# ---------------------------------------------------------------- 排程构建
def build_plan(state: str | Path = "state.json", days: int = 60,
               lead: tuple[int, int] = (1, 5), seed: int = 0,
               only_future_hours: int = 0) -> list[dict]:
    c = api.QwClient.from_state(state)
    schedule = api.flatten(c.schedule(days=days))
    buildings = c.buildings()

    rng = random.Random(seed) if seed else random.Random()
    now = dt.datetime.now()
    entries: list[dict] = []
    unresolved: list[str] = []

    for course in schedule:
        date = str(course.get("date") or "")[:10]
        start = str(course.get("start_time") or "")[:5]
        end = str(course.get("end_time") or "")[:5]
        if not date or not start:
            continue
        try:
            start_dt = dt.datetime.strptime(f"{date} {start}", "%Y-%m-%d %H:%M")
        except ValueError:
            continue

        b = api.building_of(course, buildings)
        pt = api.point_of(b)
        if not pt:
            unresolved.append(f"{date} {course.get('course_name')} {course.get('classroom')}")
            continue

        lat0, lng0 = pt
        radius = float(b.get("radius") or 150.0)
        lng, lat = geo.random_point(lat0, lng0, radius, rng=rng)

        lead_min = rng.uniform(lead[0], lead[1])
        target = start_dt - dt.timedelta(minutes=lead_min)
        if start_dt < now - dt.timedelta(hours=only_future_hours or 0):
            pass  # 过往课次也保留，方便回看；run 阶段会自动跳过

        entries.append({
            "date": date,
            "course": course.get("course_name"),
            "classroom": course.get("classroom"),
            "building": b.get("name"),
            "building_id": b.get("id"),
            "radius_m": radius,
            "start": start,
            "end": end,
            "session_id": None,   # 不猜：服务端按当前时刻下发，猜出来的会被判 session_mismatch
            "schedule_id": course.get("schedule_id"),
            "student_course_id": course.get("enrollment_id") or course.get("student_course_id"),
            "target_at": target.strftime("%Y-%m-%dT%H:%M:%S"),
            "lead_min": round(lead_min, 2),
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "offset_m": round(_dist_m(lat0, lng0, lat, lng), 1),
            "photo": None,
            "state": "pending",
            "receipt": None,
        })

    entries.sort(key=lambda e: e["target_at"])
    if unresolved:
        print(f"[!] {len(unresolved)} 节课匹配不到楼栋坐标，已跳过：")
        for u in unresolved[:8]:
            print("    ", u)
    return entries


def _dist_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    import math
    dx = (lng2 - lng1) * 111_320.0 * math.cos(math.radians(lat1))
    dy = (lat2 - lat1) * 111_320.0
    return math.hypot(dx, dy)


def save_plan(entries: list[dict], path: Path = PLAN) -> None:
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=1), "utf-8")


def load_plan(path: Path = PLAN) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text("utf-8"))


# ---------------------------------------------------------------- 执行
def due_entries(plan: list[dict], now: dt.datetime, grace_min: int = 30) -> list[dict]:
    out = []
    for e in plan:
        if e.get("state") != "pending":
            continue
        ra = e.get("retry_after")
        if ra and now < dt.datetime.fromisoformat(ra):
            continue
        try:
            t = dt.datetime.strptime(e["target_at"], "%Y-%m-%dT%H:%M:%S")
            end = dt.datetime.strptime(f"{e['date']} {e['end']}", "%Y-%m-%d %H:%M")
        except (KeyError, ValueError):
            continue
        if now < t:
            continue
        if now > end + dt.timedelta(minutes=grace_min):
            e["state"] = "expired"
            continue
        out.append(e)
    return out


def run(plan_path: Path = PLAN, state: str | Path = "state.json",
        photo: str | None = None, dry_run: bool = False, once: bool = False,
        poll: int = 20) -> None:
    from . import sign as sign_mod

    plan = load_plan(plan_path)
    if not plan:
        raise SystemExit("[-] 没有排程，先跑 python -m qwat.autoplan --build")

    print(f"[*] 排程 {len(plan)} 条 · 待执行 "
          f"{sum(1 for e in plan if e['state'] == 'pending')} 条")

    last_auth = 0.0
    while True:
        # 每 20 分钟主动验一次会话，避免真到点时才发现过期
        if time.time() - last_auth > 1200:
            try:
                from . import auth
                auth.ensure_app(state)
                last_auth = time.time()
            except FileNotFoundError as exc:
                print(f"[auth] {exc}（可用脚本手动登录）")
                last_auth = time.time()
            except Exception as exc:  # noqa: BLE001
                print(f"[auth] 会话检查失败：{type(exc).__name__}: {exc}")
                last_auth = time.time()

        now = dt.datetime.now()
        todo = due_entries(plan, now)
        for e in todo:
            print(f"\n[{now:%H:%M:%S}] → {e['course']} {e['classroom']} "
                  f"({e['date']} {e['start']}) 目标时刻 {e['target_at'][11:]} "
                  f"偏移 {e['offset_m']}m")
            try:
                res = sign_mod.sign(
                    state=state,
                    occurrence={
                        "course_name": e["course"],
                        "classroom": e["classroom"],
                        "date": e["date"],
                        "start_time": e["start"],
                        "end_time": e["end"],
                        "building_name": e["building"],
                        "session_id": e["session_id"],
                        "student_course_id": e["student_course_id"],
                        "enrollment_id": e["student_course_id"],
                    },
                    at=(e["lat"], e["lng"]),
                    photo=e.get("photo") or photo,
                    dry_run=dry_run,
                )
            except cas.NotLoggedIn:
                print("[-] CASTGC 失效，停一轮等重新登录")
                res = {"ok": False, "reason": "cas_expired"}
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

            print(f"    ok={res.get('ok')} {res.get('reason') or ''}")
            e["attempts"] = int(e.get("attempts") or 0) + 1
            reason = str(res.get("reason") or "")
            end_dt = dt.datetime.strptime(f"{e['date']} {e['end']}", "%Y-%m-%d %H:%M")
            closed = dt.datetime.now() > end_dt

            if res.get("ok"):
                e["state"] = "done"
                e["receipt"] = (res.get("response") or {}).get("data") or reason
            elif "已经签过" in reason:
                e["state"] = "done"
            elif closed:
                e["state"] = "failed"
                e["receipt"] = reason
            else:
                wait = 30 if reason == "instance_mismatch" else 60
                e["retry_after"] = (dt.datetime.now() + dt.timedelta(seconds=wait)).isoformat()
                e["last_error"] = reason
                if reason == "instance_mismatch":
                    print(f"    服务端当前课次 {res.get('got')} ≠ 计划 {res.get('want')}，{wait}s 后重试")
            save_plan(plan, plan_path)

        if once:
            break

        pending = [e for e in plan if e["state"] in ("pending", "failed")]
        if not pending:
            print("[*] 排程全部处理完，退出")
            break
        nxt = min(dt.datetime.strptime(e["target_at"], "%Y-%m-%dT%H:%M:%S") for e in pending)
        gap = (nxt - dt.datetime.now()).total_seconds()
        time.sleep(max(2, min(poll, gap)))


def ensure_timezone() -> None:
    """排程时间是本地时间；服务器若是 UTC，所有枪都会打偏 8 小时。"""
    import os
    now = dt.datetime.now().astimezone()
    if now.utcoffset() == dt.timedelta(hours=8):
        return
    print(f"[!] 当前时区 {now.tzname()} UTC{now.utcoffset()}，不是 UTC+8")
    os.environ["TZ"] = "Asia/Shanghai"
    try:
        time.tzset()          # Linux/macOS 生效
    except AttributeError:
        return                # Windows 没有 tzset，只能提示
    print(f"[!] 已临时切到 Asia/Shanghai，现在 {dt.datetime.now():%Y-%m-%d %H:%M}")


# ---------------------------------------------------------------- CLI
def main() -> None:
    ensure_timezone()
    ap = argparse.ArgumentParser(description="全课表自动排程签到")
    ap.add_argument("--build", action="store_true", help="生成 plan.json")
    ap.add_argument("--rebuild", action="store_true", help="重摇时间与坐标")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--lead", type=float, nargs=2, default=(1, 5), metavar=("MIN", "MAX"))
    ap.add_argument("--seed", type=int, default=0, help="固定随机种子（0=每次不同）")
    ap.add_argument("--photo", default=None)
    ap.add_argument("--state", default="state.json")
    ap.add_argument("--plan", default="plan.json")
    a = ap.parse_args()

    pp = Path(a.plan)

    if a.build or a.rebuild or not pp.exists():
        entries = build_plan(a.state, a.days, tuple(a.lead), a.seed)
        save_plan(entries, pp)
        print(f"[+] {len(entries)} 条排程 → {pp.resolve()}")
        print(f"{'日期':<12}{'课程':<14}{'教室':<16}{'开始':<7}{'签到时刻':<10}{'提前':<6}{'偏移'}")
        for e in entries[:14]:
            print(f"{e['date']:<12}{e['course'][:12]:<14}{(e['classroom'] or '')[:14]:<16}"
                  f"{e['start']:<7}{e['target_at'][11:]:<10}{e['lead_min']:<6}{e['offset_m']}m")
        now = dt.datetime.now()
        fut = [e for e in entries
               if dt.datetime.strptime(e["target_at"], "%Y-%m-%dT%H:%M:%S") > now]
        if fut:
            n = fut[0]
            print(f"\n下一节: {n['date']} {n['course']} · 签到时刻 {n['target_at'][11:]} "
                  f"（提前 {n['lead_min']} 分）· 坐标 {n['lat']},{n['lng']}")

    if a.run:
        run(pp, a.state, a.photo, a.dry_run, a.once)


if __name__ == "__main__":
    main()
