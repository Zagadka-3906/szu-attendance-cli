# 快速开始（5 分钟）

只跑 HTTP 链路，不需要浏览器、不需要企业微信。前提：Python 3.10+。

```bash
git clone <repo> qw-attendance && cd qw-attendance
pip install -r requirements.txt          # requests / PyYAML / cryptography
```

## 1. 存凭据

```bash
python scripts/save_creds.py
# 学号: 2023XXXXXX
# 密码（输入时不显示）: ********
# [+] 凭据已验证并保存 → creds.bin   (Windows 走 DPAPI，仅本账户可解)
# [+] 登录态已刷新 → state.json
```

密码按站点 `encrypt.js` 的算法加密后提交（`salt` 前拼 64 位随机串 + 随机 IV，
AES-128-CBC）。凭据不会以明文进日志、进命令行历史。

## 2. 验证链路

```bash
python scripts/deploy_check.py
```

七项检查：出口 IP / 时区 / DNS / 站点可达 / 凭据可解 / 密码登录 / 铸票 / 会话 / 排程。

## 3. 生成排程

```bash
python -m qwat.autoplan --build --days 60
python scripts/show_plan.py
```

每条课次自带**随机时刻**（开课前 1–5 分钟）和**随机坐标**（楼栋围栏内 25m ~ 0.85R）。
坐标用的是服务端 `/api/v1/buildings` 自己给出的圆心与半径，不需要去现场踩点。

## 4. 常驻

```bash
# Windows
schtasks /create /tn "qw-attendance" ^
  /tr "python -m qwat.autoplan --run --photo photo.jpg" /sc onlogon /rl highest /f

# Linux
sudo cp deploy/qw-attendance.service /etc/systemd/system/
sudo systemctl enable --now qw-attendance
journalctl -u qw-attendance -f
```

时区必须是 `Asia/Shanghai`，否则所有枪偏 8 小时。`install.sh` 会处理。

## 5. 只有一种情况需要人工介入

新机器 / 新 IP 首次登录可能被 CAS 判为「非可信浏览器」，要求多因子认证：

```bash
python scripts/trust_device.py --send          # 验证码发到企业微信
python scripts/trust_device.py --code XXXXXX   # 提交，并把本机登记为可信设备
```

关键在于请求必须带设备指纹 `MULTIFACTOR_BROWSER_FINGERPRINT`（`qwat/auth.py` 会自动生成并持久化），
否则服务端会返回「认证成功」却不真正登记设备。

---

# 模块地图

```
qwat/
├─ cas.py        CAS 铸票 / 登录态还原 / 打开应用
├─ auth.py       账号密码登录（复刻站点 AES）、DPAPI 凭据、设备指纹、会话自愈
├─ api.py        业务协议：课表、楼栋、location-info、check-signin-status、photo、signin
├─ sign.py       一次完整签到（含资格校验与幂等）
├─ autoplan.py   全课表排程 + 随机时刻/坐标 + 守护进程
├─ checkin.py    浏览器兜底路径（Playwright + geolocation 覆写）
├─ capture.py    有头登录 / 抓包 / 模板生成
├─ geo.py        WGS-84 ⇄ GCJ-02、围栏内随机取点
└─ wxshim.js     页面级定位注入（企微 JSSDK / AMap / geolocation）

scripts/          诊断、抓包、部署、自检、信任设备
tests/            离线自检（shim、浏览器链路、加密等价性、登录分支、过期自愈）
deploy/           systemd 单元、install.sh、crontab
```

# 免责与边界

- 本项目只做「把已有接口按原样调用」，不修改、不攻击任何系统。
- 签到记录的解释权在教务，抓不抓取决于学校，与代码无关。
- 蓝牙信标 / WiFi BSSID / 校园网 IP 白名单这三类校验，坐标伪造无效——见 README 第 6 节。
