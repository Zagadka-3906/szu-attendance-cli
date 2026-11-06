"""坐标系与抖动。

关键：先确认服务端吃的是哪套坐标。
  - 浏览器 navigator.geolocation → WGS-84
  - 腾讯地图 / 企业微信 wx.getLocation(type='gcj02') → GCJ-02（火星坐标）
  - 高德拾取的坐标 → GCJ-02
两套坐标在深圳差 500~700 米，直接填错坐标系会落在签到围栏外。
最稳的做法：真实签到一次，抓包看服务端收到的数值，与手机 GPS 读数比对确定基准。
"""
from __future__ import annotations

import math
import random

A = 6378245.0
EE = 0.00669342162296594323


def _out_of_china(lng: float, lat: float) -> bool:
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _tlat(x: float, y: float) -> float:
    r = (-100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
         + 0.2 * math.sqrt(abs(x)))
    r += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    r += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    r += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return r


def _tlng(x: float, y: float) -> float:
    r = (300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x)))
    r += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    r += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    r += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return r


def wgs84_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    if _out_of_china(lng, lat):
        return lng, lat
    dlat = _tlat(lng - 105.0, lat - 35.0)
    dlng = _tlng(lng - 105.0, lat - 35.0)
    rad = lat / 180.0 * math.pi
    magic = math.sin(rad)
    magic = 1 - EE * magic * magic
    sq = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sq) * math.pi)
    dlng = (dlng * 180.0) / (A / sq * math.cos(rad) * math.pi)
    return lng + dlng, lat + dlat


def gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    glng, glat = wgs84_to_gcj02(lng, lat)
    return lng * 2 - glng, lat * 2 - glat


def jitter(lng: float, lat: float, meters: float = 5.0) -> tuple[float, float]:
    """±meters 米随机偏移，避免每次上报完全一致的坐标。"""
    dlat = random.uniform(-meters, meters) / 111_320.0
    dlng = random.uniform(-meters, meters) / (111_320.0 * math.cos(math.radians(lat)))
    return lng + dlng, lat + dlat


def random_point(lat: float, lng: float, radius_m: float,
                 inner_m: float = 25.0, outer_ratio: float = 0.85,
                 rng: random.Random | None = None) -> tuple[float, float]:
    """在围栏圆内随机取一点（默认落在半径 25m ~ 0.85R 的环带里）。

    留 15% 余量是为了避开服务端 distance<=radius 判定在坐标精度上的边界抖动。
    """
    rng = rng or random
    if radius_m <= 0 or inner_m < 0 or not 0 < outer_ratio < 1:
        raise ValueError("围栏半径须为正，内半径非负，外半径比例须在 0 和 1 之间")
    outer = radius_m * outer_ratio
    r = rng.uniform(min(inner_m, outer), outer)
    theta = rng.uniform(0.0, 2.0 * math.pi)
    dlat = r * math.cos(theta) / 111_320.0
    dlng = r * math.sin(theta) / (111_320.0 * math.cos(math.radians(lat)))
    return lng + dlng, lat + dlat
