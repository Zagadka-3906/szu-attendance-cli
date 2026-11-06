# qw-attendance · 深大考勤签到自动化

对 `https://qw.szu.edu.cn/attendance` 的现场探测结论、原理拆解与可运行工具链。
目标形态：**本机不开企业微信**也能进站点、把定位固定到教室、把这件事托管到云服务器按课表自动跑。

> **English**: A reverse-engineered client + scheduler for Shenzhen University's attendance
> system. CAS password login (with site-compatible AES), geofence-aware check-in using the
> building coordinates the server itself exposes, photo upload, and a daemon that signs in at a
> random moment 1–5 minutes before each class from a random point inside the building fence.
> No WeCom client required. Python 3.10+, works on Windows / Linux.

本文中的课程、教室、课次、业务 ID、回执和排程输出均为脱敏示例，不对应真实个人记录。实际参数请从当前登录账号的服务端响应获取；请勿公开凭据、Cookie、照片或个人课表。

**能力一览**

| 能力 | 模块 |
|---|---|
| CAS 账号密码登录（复刻站点 AES） | `qwat/auth.py` |
| 无企业微信访问站点 | `qwat/cas.py` |
| 楼栋围栏 + 随机坐标取点 | `qwat/api.py`、`qwat/geo.py` |
| 拍照签到（multipart 上传） | `qwat/api.py` |
| 全课表排程 + 随机时刻守护 | `qwat/autoplan.py` |
| 会话过期自动重登 | `qwat/auth.py::ensure_app` |
| 多因子「信任此设备」 | `scripts/trust_device.py` |
| 抓包 / 诊断 / 自检 | `scripts/*.py`、`tests/*.py` |

---

## 1. 实测：门禁到底是什么

2026-11-06 直接对站点做了链路追踪（`python scripts/probe.py` 可复现）：

```
GET https://qw.szu.edu.cn/attendance
 └─302→ https://authserver.szu.edu.cn/authserver/login
          ?service=https%3A%2F%2Fqw.szu.edu.cn%2Fattendance%2Fcas%2Fcallback
          %3Fredirect%3D%252Fattendance%26source%3Duser
```

到 CAS 之后，**分岔完全由 User-Agent 决定**：

| UA | CAS 行为 |
|---|---|
| 桌面 Chrome | `200` 统一身份认证页，四个 tab 齐全（用户名密码 / 手机动态码 / 企业微信扫码 / 生物识别） |
| 含 `MicroMessenger` | `302` → `/authserver/weixinQYLogin.do?qyId=ww563e8adbd544adf5` → `open.weixin.qq.com/.../authorize?scope=snsapi_base` → `"请在企业微信客户端打开链接"` |
| 含 `wxwork` | 同上 |

四个 UA 实测结果（同一 URL、同一 service 参数）：

```
desktop Chrome   200  正常登录页
wxwork 4.1.26    302  企微静默授权(snsapi_base)
MicroMessenger   302  企微静默授权(snsapi_base)
```

**这就是“只能在企业微信打开”的全部原因**：CAS 看到微信系 UA 就走 `snsapi_base` 静默授权——在企微容器里能拿到登录主体所以免密，出了容器没有主体可授权，于是被微信侧弹回“请在企业微信客户端打开”。
换成普通桌面 UA，走的就是标准表单登录，**根本不需要装企业微信**。

推论（也是本方案的地基）：

- 不存在 UA 白名单式的“只准企微进”，只是**登录策略选择器**
- 桌面浏览器 / curl / 无头 Chromium（桌面 UA）都能拿到完整登录页
- 登录一次拿到 `CASTGC`，之后可无限铸票，纯 HTTP 可跑，与企微彻底解耦

---

## 2. 认证层：三条路

### 路线 ①：桌面浏览器直接登录（最省事）
用普通 Chrome 打开 `https://qw.szu.edu.cn/attendance` → 自动落在统一身份认证页 → 三种方式任选：

- **企业微信扫码**：手机扫一下即可，电脑端无需安装任何东西（走 `login.work.weixin.qq.com` 网页扫码协议，不是 `snsapi_base`）
- **账号登录**：学号 + 密码（密码由页面 JS 用 `pwdEncryptSalt` 做 AES-CBC 前端加密，不用自己实现）
- **验证码登录**：学号 + 短信动态码

### 路线 ②：CASTGC 复用（真正“无需企微”，云端关键）
`CASTGC` 一旦拿到就能无限铸票：

```
GET /authserver/login?service=<任意 service>   （带 CASTGC）
 → 302  <service>?ticket=ST-xxxx
GET <service>?ticket=ST-xxxx                    → 业务 Cookie 落地
```

所以：**登录一次 → 抽出 CASTGC → 之后全部纯 HTTP**。`CASTGC` 通常活数小时到数天（勾“记住我”可到 7 天），过期重跑一次 `--login` 即可。这是把签到搬上云服务器的前提。

### 路线 ③：桌面企微 CDP 抓 Cookie（已有企微时的野路子）
企业微信 PC 端内置 Chromium，挂调试端口可直接导出内置浏览器 Cookie，不用在手机上装 mitmproxy 证书：

```powershell
& "C:\Program Files (x86)\WXWork\WXWork.exe" --remote-debugging-port=9222
curl.exe http://127.0.0.1:9222/json/list      # 找内置浏览器 target，再走 Network.getAllCookies
```

---

## 3. 定位层：三种实现，三种打法

| 前端取点方式 | 特征 | 打法 |
|---|---|---|
| `navigator.geolocation.getCurrentPosition` | JS 里能搜到 `geolocation` | 无头 Chromium + CDP `Emulation.setGeolocationOverride`（本仓库已实现双保险） |
| 企微 JSSDK `wx.getLocation` | 页面引 `jweixin-1.x.js`、调 `wx.config` | 坐标是前端算完再提交的，服务端只看到数字 → 抓一次真实签到包，云端按模板重放 |
| 蓝牙信标 / WiFi BSSID / 校园网出口 IP | 页面调 `wx.startBeaconDiscovery` / `getConnectedWifi` | 坐标伪造无效，见 §6 |

默认走「抓一次真实包 → 生成模板 → 云端重放」（`capture.py` + `checkin.py`）。
**坐标系必须对齐**：浏览器给 WGS-84，高德/腾讯给 GCJ-02，深圳两者差 500~700 米，填错直接落在围栏外；`qwat/geo.py` 两套转换都已备好。

---

## 4. 工具链

```
qw-attendance/
├─ qwat/
│  ├─ cas.py       CAS 铸票 / 登录态还原 / 打开应用
│  ├─ geo.py       WGS-84 ⇄ GCJ-02、坐标抖动
│  ├─ capture.py   登录态采集 + 签到接口抓包 + 模板生成
│  ├─ checkin.py   签到执行（HTTP 重放 / 浏览器驱动双路）
│  └─ daemon.py    课表驱动守护进程
├─ scripts/probe.py        复现本次侦察
├─ config.example.yaml     账号、课表、教室坐标
└─ deploy/                 systemd + crontab
```

### 四步跑通

```bash
pip install -r requirements.txt
playwright install chromium

# 1) 登录一次，落地 state.json（有头浏览器，扫码/密码/验证码都行）
python -m qwat.capture --login

# 2) 在页面上正常点一次「签到」，脚本记录全部 XHR，自动生成 template.json
python -m qwat.capture --record --seconds 240

# 3) 用模板 + 教室坐标演练一次（不真提交）
python -m qwat.checkin --config config.yaml --dry-run

# 4) 课表守护
cp config.example.yaml config.yaml      # 填课表、教室坐标、时间窗
python -m qwat.daemon --config config.yaml
```

`template.json` 形如，`{{geo.lat}}` / `{{task.xxx}}` 在提交时替换：

```json
{
  "method": "POST",
  "url": "https://qw.szu.edu.cn/attendance/api/attendance/{taskId}/checkin",
  "payload": {
    "taskId": "{{task.taskId}}",
    "latitude": "{{geo.latitude}}",
    "longitude": "{{geo.longitude}}",
    "accuracy": "{{geo.accuracy}}",
    "address": "{{geo.address}}"
  }
}
```

---

## 5. 托管到云服务器

```bash
# 服务器
git clone <你的仓库> /opt/qw-attendance && cd /opt/qw-attendance
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/playwright install --with-deps chromium

# 上传本地生成的三个文件
scp state.json template.json config.yaml ubuntu@SERVER:/opt/qw-attendance/

sudo cp deploy/qw-attendance.service /etc/systemd/system/
sudo systemctl enable --now qw-attendance
journalctl -u qw-attendance -f
```

守护进程每 30 秒醒一次，只在某节课的 `window_before ~ window_after` 区间内才拉任务列表；任务出现即提交，写进 `done.json`，重启不重复签、不串课。
`CASTGC` 过期时日志明确打印 `CASTGC 失效，需要重新登录`：本地重跑 `--login` 再把 `state.json` 传上去即可。
延长寿命的小技巧：`state.json` 每次跑完回写；在 `CASTGC` 到期前 1 小时主动重铸一次 ticket，会话寿命明显拉长。

---

## 6. 这套方案会死在哪

1. **动态口令 / 手势点名**——页面每次刷新随机码，纯重放无效，只能走浏览器驱动点页面。
2. **蓝牙信标点名**——服务端核对 beacon UUID + major/minor。坐标伪造过不了；替代是在 Android 上用 BeaconSimulator（需 Xposed/root）广播教室信标。
3. **校园网出口 IP 白名单**——服务端比对来源 IP。云服务器直连必失败，得挂校内跳板或学校 VPN。
4. **CASTGC 短命 + 强制重新认证**——每次都要短信/扫码，那就留一台常开设备做“登录态生产机”，云端定时拉 cookie。
5. **异常检测**——同账号多 IP、坐标瞬移、指纹异常都会被标记。`geo.jitter()` 给了 ±4 米抖动，UA/时区/CDP 覆写也都对齐真实设备，但指纹层面只能做到“看起来像”。

另有一个反直觉点：**不要用 wxwork UA 去跑自动化**。带了它反而被 CAS 踢进 `snsapi_base`，必须企微客户端才过得去；桌面 UA 才是自由路径。`capture.py --login` 默认就是桌面 UA。

---

## 7. 风险面

签到数据的最终解释权在教务。系统侧能留下：提交时间、坐标、IP、UA、设备指纹，以及你前后几次签到的轨迹是否自洽。
一旦出现“人不在教室但记录在教室”，处理依据是校规不是技术。工具只解决“怎么提交”，是否提交由使用者决定。

---

## 8. 故障：弹窗显示「定位服务未就绪 / 位置未知」

签到弹窗是「课程与位置 → 准备照片 → 确认结果」三段式，位置这一关过不去，底部「上传照片并签到」就是废的。
系统提示里的 `定位服务未就绪` 有两种成因，症状一样、解法一样：

| 成因 | 现场特征 |
|---|---|
| 浏览器走 `navigator.geolocation`，坐标来自操作系统位置服务 | 桌面 Chrome 在 Windows 上常见 `POSITION_UNAVAILABLE`；点「打开定位设置」会跳到系统隐私设置 |
| 页面走企微 JSSDK `wx.getLocation` | 控制台报 `getLocation:fail`；企微客户端外必然失败，因为要企微内登录主体签名 |

两种都能用 [wxshim.js](qwat/wxshim.js) 在同一层解决——它在页面脚本之前接管五条取点通道：

```
navigator.geolocation     ✔ 覆盖 getCurrentPosition / watchPosition / permissions.query
window.wx（jweixin 会覆盖进去）  ✔ getter/setter 拦截，getLocation 返回注入坐标
WeixinJSBridge            ✔ 老页面的桥接通道
AMap.Geolocation          ✔ 高德定位控件
qq.maps.Geolocation       ✔ 腾讯地图定位控件
```

### 先诊断，再动手

```powershell
python scripts/diagnose_page.py --head
```

输出会直接告诉你：`window.wx` 存不存在、`navigator.geolocation` 实测是否报错、页面加载了 `jweixin` 还是 `AMap`、控制台哪条报错、弹窗原文。截图存 `diagnose.png`。

### 再执行（browser 模式会自动走完整条链）

```powershell
python -m qwat.checkin --config config.yaml --mode browser --head --keep-open --photo photo.jpg
```

脚本会依次：注入 shim → 点「重新获取定位」→ 等 `位置未知` 消失 → 选照片 → 点「上传照片并签到」→ 截图存证。
中途每步都有截图：`step1_landing.png` / `step2_geo.png` / `step3_result.png`，失败时看 `step2_geo.png` 就知道坐标有没有喂进去。

`config.yaml` 对应改两行：

```yaml
mode: browser
photo: photo.jpg
```

### 自检（不碰学校系统）

```powershell
python tests\selftest_shim.py       # 四层取点是否都返回注入坐标
python tests\selftest_browser.py    # 复刻弹窗文案，跑通 重定位→选照片→提交→成功识别
```

两项都过，说明工具侧没问题，剩下只取决于学校接口认不认这份坐标。

### 还没覆盖的一种

若 `diagnose.py` 显示页面在调 `wx.startBeaconDiscovery`（蓝牙点名），或控制台出现 `bssid` / `wifiMac`，那定位校验根本不吃经纬度，坐标注入再准也没用，得换 BeaconSimulator（Android，需 root/Xposed）广播教室信标的 UUID。

---

## 9. 代码级真相（2026-11-06 从 dist/*.js 反推 + 线上验证）

`common.js` 里 `WeChatAPI.getLocation()` 是一个 **UA 分支**：

```js
if (this._isWeChatUA()) {        // ua 含 wxwork 或 micromessenger
    await this._waitForWx(4000);
    if (typeof wx === 'undefined') throw {type:'unavailable', message:'定位服务未就绪'};
    await this.init();           // POST /api/wechat/config 拿签名 → wx.config → wx.ready
    return this._getWeChatLocationOnce();   // wx.getLocation({type:'gcj02'})
}
return this._getBrowserLocation();          // navigator.geolocation
```

**截图里那句「定位服务未就绪」，就是走了上面这条企微分支、`wx.config` 在企微外验不过导致的。**
换句话说：只要 UA 不带 `wxwork` / `MicroMessenger`，页面根本不会碰 JSSDK，直接用浏览器定位——配合 `Emulation.setGeolocationOverride` 就通了。

### 真实接口（全部线上验证过）

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/attendance/cas/status` | 登录态 + 姓名学号 |
| GET | `/attendance/api/v1/student-schedule?days=7&student_id=ID` | 课表（含 session_id / enrollment_id） |
| GET | `/attendance/api/v1/buildings` | 87 座楼 + **圆心坐标 + 围栏半径** |
| POST | `/attendance/api/v1/location-info` | `{longitude,latitude,timestamp,student_id,target_building}` → `distance / in_range / status` |
| POST | `/attendance/api/v1/check-signin-status` | `{longitude,latitude,student_id,student_course_id,session_id}` → `can_signin / already_signed / course_instance` |
| POST | `/attendance/api/v1/attendance/photo` | multipart `photo` → `photo_path` |
| POST | `/attendance/signin` | 最终提交（payload 见下） |

提交体示例（`index.js getFlowContext()` + `submit` 适配器；ID 和时间均为占位值）：

```json
{"student_id":"<student_id>","name":"<name>","course_name":"","student_course_id":1001,
 "session_id":"<server_session_id>","classroom":"","latitude":22.5377,
 "longitude":113.935,"location_address":"<building_address>",
 "wechat_userid":"<wechat_userid>","timestamp":"<UTC_timestamp>",
 "language":"zh","photo":"<photo_path>"}
```

### 位置和资格输出示例

```
[位置] 距离示例教学楼 0米 · in_range=True · status=present
[资格] can_signin=True already_signed=False status=present · 当前可签到
```

围栏半径 175 m，楼栋圆心必然在内——所以坐标取 `buildings` 里的圆心就够了，不需要真去现场踩点。

### 命令

```powershell
python -m qwat.sign --dry-run                          # 只校验，不提交
python -m qwat.sign --photo 教室.jpg --yes              # 提交当前这节
python -m qwat.sign --course "示例课程A" --photo p.jpg --yes
python -m qwat.sign --at 22.5377 113.935 --photo p.jpg --yes   # 手动指定坐标

python -m qwat.daemon --config config.yaml --once --dry-run    # 守护进程单轮演练
python -m qwat.daemon --config config.yaml                     # 常驻
python scripts/peek.py --at 22.5377 113.935                    # 只读看状态
```

`qwat/sign.py` 不需要浏览器、不需要 shim、不需要企微：一条 HTTP 链路走完全程。`wxshim.js` 现在只在你想在页面上手动验证时才用得上。

### 照片：统一用一张纯黑图

```powershell
python scripts/make_blank_photo.py -o photo.jpg     # 1080×1440 纯黑 RGB(0,0,0)，9.2 KB
```

`config.yaml` 里写死 `photo: photo.jpg` 后，所有课次、守护进程、手动签到都会用这张图，**不需要每节课换照片**。
服务端只做 `Content-Type` 与体积上限校验（前端还会压缩到 1024 长边以内），不校验画面内容——实测纯黑图上传后返回正常 `photo_path`，签到回执 `receipt_identity_match: exact`。

### 回执结构示例（虚构数据）

```json
{"success": true, "data": {"attendance_id": 2001,
 "course": {"course_name": "示例课程A"}, "classroom": "示例教学楼A-101",
 "receipt_identity_match": "exact"}}
```

再次运行同一节课会直接短路：

```
[资格] can_signin=False already_signed=True status=already_signed · 该课次已签到，无需重复签到
signed_at: <signed_at>
```

即天然幂等，重复执行不会造出第二条记录。

---

## 10. 全课表自动排程：课前 1–5 分钟 + 围栏内随机点

`qwat/autoplan.py` 把整学期课表展开成一条条「签到任务」，每条自带随机时刻与随机坐标：

```powershell
python -m qwat.autoplan --build                 # 拉取课表 → plan.json（条数由课表决定）
python -m qwat.autoplan --run                   # 常驻，到点自动签
python -m qwat.autoplan --run --once --dry-run  # 只演练当前到点的条目
python -m qwat.autoplan --rebuild --seed 42     # 重摇时间与坐标（固定种子可复现）
```

排程规则：

| 维度 | 做法 |
|---|---|
| 时刻 | `start_time` 前 `uniform(1, 5)` 分钟，精确到秒；实测服务端 `valid_start = start - 15min`，因此课前签到合法 |
| 位置 | 取该楼栋在 `/api/v1/buildings` 里的圆心 + 随机方位，半径 `uniform(25m, 0.85R)`；留 15% 余量避开 `distance <= radius` 的边界抖动 |
| 稳定性 | 时间与坐标在 `--build` 时定死并落盘，重启不变形；要换就 `--rebuild` |
| 幂等 | 已签到的课次由 `check-signin-status` 短路，`state` 自动记 `done` |

校验输出示例（`scripts/verify_plan.py`，课程与日期已脱敏）：

```
✓ <date> 示例课程A  示例教学楼A R=175m 偏移=28m  distance=28.4
✓ <date> 示例课程B  示例教学楼B R=135m 偏移=87m  distance=87.04
...
结果: 10/10 在围栏内
```

围栏判定是真在校验，不是摆设（`scripts/fence_test.py`）：

```
22.5377,113.935   distance=0        valid=True   present
22.5337,113.9337  distance=464.39   valid=False  outside_range   ← 超出 175m 半径即拒
22.6100,114.0290  distance=12561.2  valid=False  outside_range
```

### 常驻方式

Windows（用户登录后自动跑）：

```powershell
schtasks /create /tn "qw-attendance" /tr "C:\path\to\qw-attendance\.venv\Scripts\python.exe -m qwat.autoplan --run --photo photo.jpg" /sc onlogon /rl highest /f
```

Linux 服务器：见 `deploy/qw-attendance.service`，把 `ExecStart` 换成
`.venv/bin/python -m qwat.autoplan --run --photo photo.jpg`。

课表覆盖范围取决于服务端返回值和 `--days` 参数；跨学期或计划用完后 `--rebuild` 一次即可。

### 一个必须知道的坑：`session_id` 不能猜

`session_id` 看起来能由 `schedule_id:date:start` 拼出来，**但服务端只认它自己下发的值**，应直接使用响应中的值。
自己拼一个去请求会直接返回：

```json
{"code": "session_mismatch", "message": "课次信息已变化，请重新选择课程", "success": false}
```

正确做法：`check-signin-status` 的 `session_id` 传 `null`，服务端按**当前时刻**返还对应 `course_instance`，payload 再从里面取。
所以 `sign()` 里加了一道守卫：返回课次的 `date/start_time` 与计划不符时，判为 `instance_mismatch` 并等待重试，**绝不误判成"已签到"**。
没有这道守卫，后续课次可能被上一节的 `already_signed` 顶掉、静默跳过。

---

## 11. 凭据保存与自动重登

`qwat/auth.py` 把 CAS 的账号密码登录完整实现了，会话过期时会自动用保存的凭据重登，`state.json` 原地刷新。

### 密码加密（复刻站点 encrypt.js）

```js
getAesString(randomString(64) + password, salt, randomString(16))
// key = Utf8(salt)   iv = Utf8(random16)   AES/CBC/PKCS7 → base64(密文)
```

Python 侧用 `cryptography` 还原同一套：明文前拼 64 位随机串、IV 随机 16 位、密钥就是页面下发的 `pwdEncryptSalt`。
**等价性做过双向验证**（`tests/selftest_encrypt.py`，直接用站点自己的 CryptoJS 对拆）：

```
A1 站点自解      ✅   A2 Python 解站点 ✅   B  站点解我们的  ✅
结果: 实现与站点完全等价 ✅
```

### 凭据存哪

```powershell
python scripts/save_creds.py
学号: 2023XXXXXX
密码（输入时不显示）: ********
[*] 先验证一次，确认凭据可用 …
[+] 凭据已验证并保存 → creds.bin（DPAPI 加密，仅当前 Windows 账户可解）
[+] 登录态已刷新 → state.json
```

- Windows 上 `creds.bin` 是 **DPAPI 密文**，换用户/换机器都解不开
- 密码用 `getpass` 输入，不进命令行历史、不进日志
- 不想用二进制文件也可以写 `config.yaml` 的 `account.student_id` / `account.password`（明文，二选一）

### 自愈逻辑

```
api.QwClient.from_state()
  → auth.ensure_app()
      ├─ state.json 有效 → 直接用
      └─ CASTGC 失效 → load_creds() → CAS 密码登录 → 回写 state.json → 继续
```

守护进程每 20 分钟主动验一次会话，不等到上课那一刻才发现过期。

### 验证码是唯一的变数

`captchaSwitch = "2"`，服务端按 `POST /checkNeedCaptcha.htl` 的 `isNeed` 决定要不要图形验证码（按 IP 与失败次数启发式判定）。
需要时 `auth.login` 不会硬闯，直接抛 `NeedCaptcha` 并把验证码存成 `captcha.png`；此时在浏览器里手动登录一次（`python -m qwat.capture --login`）即可把风控等级降回去，凭据仍然有效。

自检：

```powershell
python tests\selftest_encrypt.py        # 加密等价性
python tests\selftest_login_branch.py   # 验证码分支识别
```

---

## 12. 部署到服务器 / 要不要校园网

### 结论：不需要校园网

实测证据（2026-11-06）：

| 检查 | 结果 |
|---|---|
| 真实解析 | `qw.szu.edu.cn → 210.39.4.1`、`authserver → 210.39.12.28`（公网可达的校园地址，非内网 10./172.） |
| 从非校园网络访问 CAS 登录页 | **HTTP 200**，完整登录页 |
| 从非校园网络访问 `GET /attendance/api/v1/buildings` | **HTTP 200 + 87 座楼全量数据**，不带任何 Cookie |

也就是说：站点对外公开可达，**没有 IP 白名单**；而签到围栏判定吃的是请求体里的经纬度（前端算好再提交），跟你的出口 IP 毫无关系。
服务器放哪儿都行，甚至放海外也能签——只是风控上不划算。

### 唯一有意义的差异：出口 IP 影响验证码

`checkNeedCaptcha` 是按 IP/失败次数做启发式判断的。用一台全新的云主机 IP 登录，可能第一次就要求图形验证码。
所以：

- 优先选**大陆、最好广东**的机房（腾讯云广州/深圳、阿里云华南），延迟低、IP 段"正常"
- 别用被大量脚本滥用过的廉价 VPS 出口 IP
- 真撞上验证码：在能开浏览器的机器上 `python -m qwat.capture --login`，把 `state.json` 传到服务器即可，`creds.bin` 留着以后重登

### 迁移时三个会踩的坑

**① `creds.bin` 不能拷到 Linux。** DPAPI 绑当前 Windows 账户，换机器解不开。服务器上重新 `python scripts/save_creds.py`（Linux 分支存成 0600 权限的本地文件），或在 `config.yaml` 写明文凭据。

**② 时区必须是 Asia/Shanghai。** `plan.json` 里存的是本地时间，服务器跑 UTC 会让所有枪偏 8 小时。`autoplan` 启动时会检测并尝试用 `TZ` + `time.tzset()` 纠正，systemd 单元里也写死了 `Environment=TZ=Asia/Shanghai`，`install.sh` 还会去 `timedatectl set-timezone`。

**③ `state.json` 可以拷，但别指望长命。** Cookie 通常不绑 IP，拷过去能用；过期了服务器侧会自己用凭据重登，前提是坑①解决了。

### 部署三步

```bash
# 1. 放代码
sudo mkdir -p /opt/qw-attendance && sudo chown $USER /opt/qw-attendance
cd /opt/qw-attendance && # scp/git 把项目传上来

# 2. 自检（会真的登一次、铸一次票）
bash deploy/install.sh

# 3. 看日志
journalctl -u qw-attendance -f
```

`deploy/install.sh` 依次做：时区 → venv + 依赖 → `scripts/deploy_check.py` 全链路自检 → systemd 安装启动。
自检不过就退出，不会把半残的服务装上去。

`scripts/deploy_check.py` 的输出示例（账号、时间、票据和排程均已脱敏）：

```
[网络] 出口 IP: ...
[时区] UTC+8 · 本地 <local_datetime>
[DNS ] qw.szu.edu.cn → 210.39.4.1
[站点] /api/v1/buildings → 200，87 座楼
[凭据] creds.bin 存在 · 可读出 2023XXXXXX
[登录] 密码登录成功 耗时 0.33s
[铸票] <redacted_ticket>
[会话] logged_in=True 同学A
[排程] <total> 条，待执行 <pending> 条，下次执行 <next_target_at>
✅ 全部通过，可以托管
```

### 三种托管形态对比

| 形态 | 优点 | 代价 |
|---|---|---|
| 云服务器（Linux systemd） | 7×24 稳定，重启自愈 | 要维护一台机器；新 IP 可能触发验证码 |
| 家里闲置小主机 / NAS / 软路由 | 出口 IP 就是家里宽带，最"正常" | 断电断网就停；需自己保活 |
| 旧安卓机 Termux | 移动网络出口，随身 | 系统杀后台、休眠断连，最不稳定 |

三种都跑同一套代码，`plan.json` / `photo.jpg` / 凭据是唯一要带过去的东西。
