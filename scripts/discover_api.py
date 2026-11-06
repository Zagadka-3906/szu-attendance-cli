"""登录态下扒前端：找出真实 API、定位实现、上传方式。

python scripts/discover_api.py

产出：
  dist/index.html            应用外壳
  dist/*.js                  构建产物
  dist/report.txt            接口 / 定位 / 上传 三类命中点
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwat import cas  # noqa: E402

OUT = Path("dist")


def fetch(s, url: str) -> tuple[int, str]:
    r = s.get(url, timeout=30)
    return r.status_code, r.text


def main() -> None:
    OUT.mkdir(exist_ok=True)
    s = cas.new_session()
    data = __import__("json").loads(Path("state.json").read_text("utf-8"))
    for c in data.get("cookies", []):
        s.cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))

    try:
        cas.open_app(s)
    except cas.NotLoggedIn as exc:
        sys.exit(f"[-] {exc}")

    code, html = fetch(s, cas.APP + "/")
    print(f"[+] /attendance/ → {code}, {len(html)} bytes")
    (OUT / "index.html").write_text(html, "utf-8")
    if code != 200:
        sys.exit("[-] 未拿到应用外壳")

    # 收集静态资源
    assets = set(re.findall(r'(?:src|href)="([^"]+\.(?:js|css))(?:\?[^"]*)?"', html))
    assets |= set(re.findall(r'"(/attendance/[^"]+\.js)"', html))
    print(f"[+] 发现 {len(assets)} 个静态资源")

    blobs: list[tuple[str, str]] = []
    for a in sorted(assets):
        url = urljoin(cas.APP + "/", a)
        try:
            c, body = fetch(s, url)
        except Exception as exc:  # noqa: BLE001
            print(f"    ! {a}: {exc}")
            continue
        name = re.sub(r"[^\w.\-]", "_", url.rsplit("/", 1)[-1])[:80]
        (OUT / name).write_text(body, "utf-8")
        blobs.append((url, body))
        print(f"    {c} {len(body):>8}  {name}")

    # 命中点
    pats = {
        "接口": r'["\'`](/attendance/api/[A-Za-z0-9_\-/{}.$]+)["\'`]',
        "上传": r'(upload|multipart|FormData|\.jpg|\.png)',
        "定位": r'(getLocation|geolocation|latitude|longitude|gcj02|wgs84|accuracy)',
        "企微": r'(jweixin|wx\.config|agentConfig|getSignature|beacon|bssid|wifiMac)',
        "签到": r'(checkin|checkIn|signIn|attendance|qiandao|qd)',
    }
    lines: list[str] = []
    for label, pat in pats.items():
        hits: set[str] = set()
        for url, body in blobs:
            for m in re.finditer(pat, body, re.I):
                snippet = body[max(0, m.start() - 60): m.end() + 60].replace("\n", " ")
                hits.add(snippet.strip()[:200])
        lines.append(f"\n===== {label}（{len(hits)} 处）=====")
        for h in sorted(hits)[:40]:
            lines.append("  " + h)

    report = "\n".join(lines)
    (OUT / "report.txt").write_text(report, "utf-8")
    print(report[:4000])
    print(f"\n[+] 完整报告 → {OUT/'report.txt'}")


if __name__ == "__main__":
    main()
