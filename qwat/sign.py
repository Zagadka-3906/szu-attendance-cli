"""一键签到（真实协议版）。

流程与网页完全一致：
  cas/status → student-schedule → buildings(取围栏圆心) → location-info(校验在范围内)
  → check-signin-status(确认可签) → attendance/photo(上传) → signin(提交)

用法：
  python -m qwat.sign --dry-run                    # 只算坐标、只校验资格，不提交
  python -m qwat.sign --photo photo.jpg --yes      # 真提交
  python -m qwat.sign --photo p.jpg --course "示例课程A" --yes
  python -m qwat.sign --photo p.jpg --at 22.5377 113.935 --yes   # 手动指定坐标
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import api, cas, geo


def build_payload(user: dict, instance: dict, lat: float, lng: float,
                  address: str, photo_path: str) -> dict:
    """严格复刻 index.js getFlowContext() 的 payload 结构（auto 模式）。"""
    return {
        "student_id": user.get("username") or user.get("student_id"),
        "name": user.get("name"),
        "course_name": "",                      # auto 模式留空，由服务端按 student_course_id 回填
        "student_course_id": instance.get("enrollment_id") or instance.get("student_course_id"),
        "session_id": instance.get("session_id"),
        "classroom": "",
        "latitude": round(lat, 6),
        "longitude": round(lng, 6),
        "location_address": address,
        "wechat_userid": user.get("username") or user.get("student_id"),
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")
                        .replace("+00:00", "Z"),
        "language": "zh",
        "photo": photo_path,
    }


def sign(state: str | Path = "state.json", course_name: str | None = None,
         at: tuple[float, float] | None = None, photo: str | None = None,
         dry_run: bool = False, jitter_m: float = 0.0,
         verbose: bool = True, occurrence: dict | None = None) -> dict:
    c = api.QwClient.from_state(state)

    st = c.status()
    if not st.get("logged_in"):
        raise cas.NotLoggedIn("CAS 会话失效，先跑 python -m qwat.capture --login")
    user = st.get("user") or {}

    schedule = c.schedule()
    if occurrence:
        target = occurrence
    elif course_name:
        cands = [x for x in api.flatten(schedule) if x.get("course_name") == course_name]
        if not cands:
            names = sorted({str(x.get("course_name")) for x in api.flatten(schedule)})
            return {"ok": False, "reason": f"课表里没有「{course_name}」，可选：{names}"}
        target = api.pick_occurrence(cands)
    else:
        target = api.current_or_next(schedule)
    if not target:
        return {"ok": False, "reason": "今天没有可签到的课"}

    # 坐标：手动 > 楼栋圆心
    if at:
        lat, lng = at
        addr = f"广东省深圳市南山区 {target.get('building_name') or ''}".strip()
    else:
        b = api.building_of(target, c.buildings())
        pt = api.point_of(b)
        if not pt:
            return {"ok": False, "reason": "课表楼栋在 buildings 里匹配不到坐标"}
        lat, lng = pt
        addr = (b.get("address") or "") + (b.get("name") or "")
    if jitter_m:
        lng, lat = geo.jitter(lng, lat, jitter_m)

    if verbose:
        print(f"[*] {user.get('name')} {user.get('username')} · "
              f"{target.get('course_name')} · {target.get('classroom')} · "
              f"{str(target.get('start_time'))[:5]}-{str(target.get('end_time'))[:5]}")
        print(f"[*] 坐标 {lat},{lng} ({addr})")

    loc = c.location_info(lat, lng, user.get("username"), target.get("building_name"))
    ld = loc.get("data") or {}
    inside = bool(ld.get("is_valid_location"))
    if verbose:
        print(f"[位置] {ld.get('message')} · in_range={inside} · status={ld.get('status')}")
    if not inside:
        return {"ok": False, "reason": "坐标不在该楼栋围栏内", "location": ld}

    chk = c.check_status(lat, lng, user.get("username"),
                         target.get("student_course_id") or target.get("enrollment_id"),
                         target.get("session_id"))
    cd = chk.get("data") or {}
    if not cd and chk.get("code"):
        if verbose:
            print(f"[资格] {chk.get('code')} · {chk.get('message')}")
        return {"ok": False, "reason": f"{chk.get('code')}: {chk.get('message')}", "raw": chk}
    if verbose:
        print(f"[资格] can_signin={cd.get('can_signin')} already_signed={cd.get('already_signed')} "
              f"status={cd.get('status')} · {cd.get('message')}")

    # 服务端按「当前时刻」决定这通请求对应哪一节课；若不是计划里的那节，就别动手
    instance = cd.get("course_instance") or {}
    want_date, want_start = target.get("date"), str(target.get("start_time") or "")[:5]
    got_date, got_start = instance.get("date"), str(instance.get("start_time") or "")[:5]
    if ((want_start and got_start != want_start) or
            (want_date and got_date != want_date)):
        if verbose:
            print(f"[!] 服务端当前课次是 {got_date} {got_start}，不是计划的 {want_date} {want_start}")
        return {"ok": False, "reason": "instance_mismatch",
                "got": f"{got_date} {got_start}", "want": f"{want_date} {want_start}"}

    if cd.get("already_signed"):
        return {"ok": True, "reason": "本节课已经签过", "data": cd}
    if not cd.get("can_signin"):
        return {"ok": False, "reason": cd.get("message") or "当前不可签到", "data": cd}

    if not (instance.get("enrollment_id") or instance.get("student_course_id")) or not instance.get("session_id"):
        return {"ok": False, "reason": "服务端未给出 course_instance（session_id 缺失）", "data": cd}

    payload = build_payload(user, instance, lat, lng, addr, "<photo_path>")
    if dry_run:
        if verbose:
            print("\n[dry-run] 将要 POST /attendance/signin：")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return {"ok": True, "dry_run": True, "payload": payload}

    if not photo:
        return {"ok": False, "reason": "拍照签到需要 --photo 指定照片"}
    photo_path = c.upload_photo(photo)
    if verbose:
        print(f"[照片] 已上传 → {photo_path}")

    payload["photo"] = photo_path
    res = c.signin(payload)
    ok = bool(res.get("success"))
    if verbose:
        print(f"[提交] success={ok} {json.dumps(res, ensure_ascii=False)[:400]}")
    return {"ok": ok, "response": res, "payload": payload}


def main() -> None:
    ap = argparse.ArgumentParser(description="按真实协议完成一次签到")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--state", default=None, help="缺省读 config.yaml 的 state_file")
    ap.add_argument("--course", default=None)
    ap.add_argument("--at", nargs=2, type=float, metavar=("LAT", "LNG"), default=None)
    ap.add_argument("--photo", default=None, help="缺省读 config.yaml 的 photo")
    ap.add_argument("--jitter", type=float, default=None, help="坐标抖动半径（米）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true", help="确认真的提交")
    a = ap.parse_args()

    cfg = {}
    cfg_path = Path(a.config)
    if cfg_path.exists():
        import yaml
        cfg = yaml.safe_load(cfg_path.read_text("utf-8")) or {}

    if not a.dry_run and not a.yes:
        sys.exit("[-] 真提交需要加 --yes（先跑 --dry-run 看一遍）")

    state = a.state or cfg.get("state_file", "state.json")
    photo = a.photo or cfg.get("photo")
    jitter = a.jitter if a.jitter is not None else float(cfg.get("jitter_m", 0) or 0)
    at = tuple(a.at) if a.at else None

    if not a.dry_run and not photo:
        sys.exit("[-] 没有照片：用 --photo 指定，或在 config.yaml 里写 photo:")
    if not a.dry_run and photo and not Path(photo).is_file():
        sys.exit(f"[-] 照片不存在：{photo}，请提供自己准备的 JPEG 照片")

    res = sign(state, a.course, at, photo, a.dry_run, jitter)
    print("\n结果:", json.dumps(res, ensure_ascii=False)[:1200])
    sys.exit(0 if res.get("ok") else 2)


if __name__ == "__main__":
    main()
