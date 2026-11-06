"""qw.szu.edu.cn/attendance 的真实协议客户端。

业务调用链路：

  GET  /attendance/cas/status                      → {logged_in, user:{username,name}}
  GET  /attendance/api/v1/student-schedule?days=7&student_id=ID
  GET  /attendance/api/v1/buildings                → 楼栋 + 坐标（服务端自己给的围栏圆心）
  POST /attendance/api/v1/location-info            {longitude,latitude,timestamp,student_id,target_building}
  POST /attendance/api/v1/check-signin-status      {longitude,latitude,student_id,student_course_id,session_id}
  POST /attendance/api/v1/attendance/photo         multipart photo → {data:{photo_path}}
  POST /attendance/signin                          → 最终签到

关键结论：
  · 坐标作为请求参数提交，是否接受由服务端当前策略决定
  · 定位分支由 UA 决定：wxwork/MicroMessenger 走 wx.getLocation（必须企微内），
    桌面 UA 走 navigator.geolocation（任何浏览器都行）
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import requests

from . import cas

BASE = "https://qw.szu.edu.cn/attendance"


class QwClient:
    def __init__(self, session: requests.Session):
        self.s = session
        self.s.headers.setdefault("Accept", "application/json")

    # ---------------------------------------------------------------- 构造
    @classmethod
    def from_state(cls, path: str | Path = "state.json") -> "QwClient":
        from . import auth
        s = auth.ensure_app(path)          # 失效自动用保存的凭据重登
        return cls(s)

    # ---------------------------------------------------------------- 读
    def status(self) -> dict:
        r = self.s.get(f"{BASE}/cas/status", timeout=20)
        return r.json()

    def schedule(self, days: int = 7, student_id: str | None = None) -> list[dict]:
        sid = student_id or (self.status().get("user") or {}).get("username")
        r = self.s.get(f"{BASE}/api/v1/student-schedule",
                       params={"days": days, "student_id": sid}, timeout=20)
        data = r.json().get("data") or {}
        return data.get("schedule") or []

    def buildings(self) -> list[dict]:
        r = self.s.get(f"{BASE}/api/v1/buildings", timeout=20)
        data = r.json().get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("buildings") or data.get("list") or []
        return []

    def location_info(self, lat: float, lng: float, student_id: str,
                      target_building: str | None = None) -> dict:
        body = {
            "longitude": lng, "latitude": lat,
            "timestamp": int(dt.datetime.now().timestamp()),
            "student_id": student_id,
        }
        if target_building:
            body["target_building"] = target_building
        r = self.s.post(f"{BASE}/api/v1/location-info", json=body, timeout=20)
        return r.json()

    def check_status(self, lat: float, lng: float, student_id: str,
                     student_course_id=None, session_id: str | None = None) -> dict:
        body = {"longitude": lng, "latitude": lat, "student_id": student_id,
                "student_course_id": student_course_id, "session_id": session_id}
        r = self.s.post(f"{BASE}/api/v1/check-signin-status", json=body, timeout=20)
        return r.json()

    # ---------------------------------------------------------------- 写
    def upload_photo(self, path: str | Path) -> str:
        with open(path, "rb") as fh:
            r = self.s.post(f"{BASE}/api/v1/attendance/photo",
                            files={"photo": ("attendance.jpg", fh, "image/jpeg")},
                            timeout=60)
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"照片上传失败: {data}")
        return data["data"]["photo_path"]

    def signin(self, payload: dict) -> dict:
        r = self.s.post(f"{BASE}/signin", json=payload, timeout=30)
        try:
            return r.json()
        except Exception:
            return {"success": False, "http": r.status_code, "raw": r.text[:500]}


# ---------------------------------------------------------------- 课表解析
def flatten(schedule: list[dict]) -> list[dict]:
    """兼容两种返回：day.courses[] 与 直接是课程对象的平铺列表。"""
    out: list[dict] = []
    for day in schedule or []:
        date = day.get("date")
        courses = day.get("courses")
        if courses:
            for c in courses:
                out.append({**c, "date": c.get("date") or date})
        elif day.get("course_name"):
            out.append(dict(day))
    return out


def current_or_next(schedule: list[dict], now: dt.datetime | None = None) -> dict | None:
    """从 /student-schedule 的返回里挑出当前这节课；没有就挑下一节。"""
    now = now or dt.datetime.now()
    today = now.strftime("%Y-%m-%d")
    flat = flatten(schedule)

    def minutes(hhmm: str) -> int:
        hh, mm = str(hhmm)[:5].split(":")
        return int(hh) * 60 + int(mm)

    cur = None
    for c in flat:
        if c.get("date") != today:
            continue
        try:
            if minutes(c["start_time"]) <= now.hour * 60 + now.minute <= minutes(c["end_time"]):
                cur = c
        except Exception:
            continue
    if cur:
        return cur
    future = [c for c in flat if c.get("date") == today and
              minutes(c.get("start_time", "23:59")) > now.hour * 60 + now.minute]
    future.sort(key=lambda c: c.get("start_time", ""))
    return future[0] if future else None


def pick_occurrence(cands: list[dict], now: dt.datetime | None = None) -> dict | None:
    """同名课可能一周多次：优先正在上的，其次今天还没开始的，最后取最近一次。"""
    now = now or dt.datetime.now()
    today = now.strftime("%Y-%m-%d")
    mins = now.hour * 60 + now.minute

    def to_min(v) -> int:
        try:
            hh, mm = str(v)[:5].split(":")
            return int(hh) * 60 + int(mm)
        except Exception:
            return 10 ** 6

    todays = [c for c in cands if str(c.get("date"))[:10] == today]
    for c in todays:
        if to_min(c.get("start_time")) <= mins <= to_min(c.get("end_time")):
            return c
    upcoming = sorted([c for c in todays if to_min(c.get("start_time")) > mins],
                      key=lambda c: c.get("start_time", ""))
    if upcoming:
        return upcoming[0]
    if todays:
        return sorted(todays, key=lambda c: c.get("start_time", ""))[-1]
    return sorted(cands, key=lambda c: (str(c.get("date")), c.get("start_time", "")))[0]


def building_of(course: dict, buildings: list[dict]) -> dict | None:
    """按课表里的 building_name / classroom 前缀匹配楼栋，拿到服务端的圆心坐标。"""
    want = [str(course.get(k) or "").strip() for k in
            ("building_name", "building_name_en", "classroom")]
    want = [w for w in want if w]
    for b in buildings:
        names = [str(b.get(k) or "").strip() for k in ("name", "name_en")]
        for w in want:
            if any(n and (w == n or w.startswith(n)) for n in names):
                return b
    for w in want:
        for b in buildings:
            if str(b.get("name") or "") and str(b["name"]) in w:
                return b
    return None


def point_of(building: dict | None) -> tuple[float, float] | None:
    if not building:
        return None
    for lat_k, lng_k in (("latitude", "longitude"), ("lat", "lng"),
                         ("gps_latitude", "gps_longitude")):
        if building.get(lat_k) is not None and building.get(lng_k) is not None:
            try:
                return float(building[lat_k]), float(building[lng_k])
            except (TypeError, ValueError):
                continue
    return None
