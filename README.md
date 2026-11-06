# qw-attendance

深圳大学考勤系统的 Python 命令行客户端。可用自己的账号获取课表、检查签到资格、上传照片并提交签到，也可生成课次计划并常驻执行。没有独立的网页管理界面。

## 能做到什么

| 功能 | 实际行为 |
| --- | --- |
| 账号登录 | 调用学校 CAS 密码登录，保存本地凭据和会话；也可用浏览器手动登录 |
| 会话恢复 | 优先使用已有会话，失效后尝试用本机保存的账号密码重新登录 |
| 查询 | 读取本人课表、楼栋信息、位置校验和当前课次的签到资格 |
| 单次签到 | 校验位置与课次，上传 JPEG 照片，再提交；已签到时跳过 |
| 自动排程 | 默认获取未来 60 天课表，将随机执行时间和楼栋范围内的坐标保存到本地计划 |
| 计划执行 | 默认在课前 1～5 分钟执行，失败时在有效期内重试，记录完成状态与回执 |
| 手工课表 | 可另用 YAML 配置每周课程，在指定时间窗口内轮询 |
| 浏览器辅助 | 用 Playwright 登录、诊断定位或操作签到页面；需额外安装 |

默认坐标取自服务端楼栋信息，**不是对设备真实位置的测量或证明**。单次签到支持手动指定坐标；排程默认在楼栋范围内随机取点。服务端仍会检查位置、课次和可签到时间。

## 运行前提和限制

- Python **3.10 或更新版本**；HTTP 主流程支持 Windows 和 Linux。后台服务安装脚本面向使用 systemd 的 Ubuntu/Debian。
- 需要你自己的有效学校账号，且运行机器能访问学校 CAS 和考勤网站。
- 排程以 **UTC+8 本地时间**执行；Windows 请设置为 UTC+8，Linux 服务已设置 `TZ=Asia/Shanghai`。
- 自行准备签到所需的 **JPEG 照片**。仓库不包含账号、Cookie、个人课表、照片或可直接执行的计划。
- 图形验证码、短信或企业微信二次认证需要人工完成，不能保证永远免登录或无人值守。
- 本工具只适配现有接口；接口变化、蓝牙/WiFi 校验、来源 IP 限制等策略可能使它不可用。不能据此保证任意网络或服务器都能签到。
- 没有自动跨学期更新、通知推送或多账号管理。课表变化后需重新生成计划并重启执行器。

请按学校规定使用自己的账号和考勤资料。命令输出、截图和计划中可能包含个人信息，不要公开上传。

## 1. 安装

```bash
git clone https://github.com/Zagadka-3906/qw-attendance.git
cd qw-attendance
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

若 PowerShell 不允许激活，后续命令中的 `python` 可直接换成 `.\.venv\Scripts\python.exe`。

Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.txt` 会安装当前项目及 HTTP 核心依赖；HTTP 主流程不需要 Chromium。以下命令均在项目根目录执行，并使用同一个虚拟环境。

## 2. 用自己的账号登录

```bash
python scripts/save_creds.py
```

按提示输入学号和密码。脚本验证登录成功后才保存 `creds.bin` 和 `state.json`。密码不显示，也不需要写进命令行参数。

凭据存储方式：

- Windows：使用 DPAPI 加密，仅当前 Windows 用户可解密。
- Linux：`creds.bin` 内为带标记的**明文 JSON**，权限设为 `0600`；它不是加密文件。
- Windows 的 `creds.bin` 不能复制到 Linux 使用。迁移后应在目标机器、以运行服务的用户重新保存凭据。
- 仅保存浏览器会话也能使用，但会话过期后，若没有本机凭据就无法自动重登。

如果需要验证码或扫码，安装浏览器辅助功能后手动登录：

```bash
python -m pip install -e ".[browser]"
python -m playwright install chromium
python -m qwat.capture --login
```

该命令需要能显示窗口的桌面环境。无桌面的服务器可接收你从桌面机器安全复制的 `state.json`，但不能保证换机器后会话仍有效。浏览器登录不会自动保存密码。

二次认证也可使用辅助脚本；仅在确有需要时运行，`--send` 会真的发送验证码：

```bash
python scripts/trust_device.py --send
python scripts/trust_device.py --code YOUR_CODE
```

不同站点验证方式可能变化；辅助脚本失败时，优先使用可见浏览器完成验证。

## 3. 查询与单次演练

```bash
# 查看登录态、本人课表和楼栋匹配，不提交签到
python scripts/peek.py

# 检查今天当前或下一节课的位置和资格，不上传照片、不提交
python -m qwat.sign --dry-run

# 指定课表中的完整课程名称
python -m qwat.sign --course "你的课程名称" --dry-run
```

`--dry-run` 仍会访问学校接口，也可能刷新本机会话；它不是离线模式。如果没有课程、位置不通过或尚未开放签到，会返回原因。课次以服务端返回值为准，不应自行拼接 `session_id`。

确认要提交后，用你准备的照片执行：

```bash
python -m qwat.sign --photo /path/to/classroom.jpg --yes
```

请替换成真实存在的路径。Windows 路径示例为 `"C:\path\to\classroom.jpg"`。需要手动传位置时使用 `--at LAT LNG`，顺序为纬度、经度。最终成功与否以服务端回执为准。

## 4. 自动生成和执行计划

```bash
# 生成计划；--days 指请求的天数，实际覆盖范围以服务端返回为准
python -m qwat.autoplan --build --days 60
python scripts/show_plan.py

# 检查当前到点的任务，只执行一轮演练，不改计划状态
python -m qwat.autoplan --run --once --dry-run

# 准备好照片后检查部署条件；会访问学校接口，但不签到
python scripts/deploy_check.py --photo /path/to/classroom.jpg

# 正式常驻，到点会自动上传照片并提交，不再逐次询问
python -m qwat.autoplan --run --photo /path/to/classroom.jpg
```

计划保存在 `plan.json`，完成状态和回执也在该文件中；重启继续使用同一计划。每条计划可以单独设置 `photo` 路径，否则使用 `--photo` 的照片。启动时会检查待执行任务的照片是否存在。

排程行为：

- `--lead MIN MAX` 设置提前分钟数，默认 `1 5`；不保证自定义时间仍在服务端允许窗口内。
- 计划生成时确定时间和坐标，重启不会重新随机。
- 课次不匹配时等待重试，避免将上一课次的结果当作本节结果。
- `done` 表示服务端已接受或该课次已签到；超过处理期限的任务标记为 `expired`，结束后仍失败的标记为 `failed`。
- 演练只检查当前到点任务并退出，不预演未来课次，也不会将其标记为完成。
- 计划全部处理完后执行器退出；不会自动生成下学期计划。

计划已存在时，`--build` 会拒绝覆盖。确认需要更换计划后：

```bash
# 先停止正在运行的执行器，再重建、检查、重新启动
python -m qwat.autoplan --rebuild --days 60
python scripts/show_plan.py
```

`--rebuild` 会覆盖原计划及本地状态；服务端已有签到记录仍会通过资格查询识别。不要让多个执行器同时使用同一计划或账号。

## 5. 后台运行

### Linux systemd

以将要运行服务的用户，先完成安装、登录和计划生成，将照片放在项目根目录并命名为 `photo.jpg`，然后运行：

```bash
bash deploy/install.sh
journalctl -u qw-attendance -f
```

脚本会安装系统依赖，在当前项目目录使用 `.venv`，检查会话、计划、照片后，生成并启动服务。服务采用执行安装脚本的用户和当前目录，不要求固定安装到 `/opt`；路径不能含空白或 `%`。需要 `sudo`，不要通过 `sudo bash` 切换成另一用户读取现有凭据。

```bash
sudo systemctl stop qw-attendance
# 重建并检查计划后：
sudo systemctl restart qw-attendance
```

`deploy/qw-attendance.service` 是默认路径模板；安装脚本会替换用户和路径。服务仅在异常退出时自动重启，计划处理完正常退出后不会反复启动。

### Windows

可以保持终端运行，或在任务计划程序中创建“登录时”任务：

- 程序：项目虚拟环境内 `.venv\Scripts\python.exe` 的绝对路径。
- 参数：`-m qwat.autoplan --run --photo photo.jpg`。
- “起始于”：项目根目录的绝对路径；缺少它会找不到凭据、计划和照片。
- 使用保存凭据的同一 Windows 用户运行。

## 手工维护每周课表（可选）

需要自己维护星期、课程名称和时间窗口时，复制 `config.example.yaml` 为 `config.yaml`，替换所有虚构课程和照片路径：

```bash
python -m qwat.daemon --config config.yaml --once --dry-run
python -m qwat.daemon --config config.yaml
```

这个入口每 60 秒检查一次，按“开课前 `window_before` 分钟，到开课后 `window_after` 分钟”决定是否执行；完成记录保存在 `done.json`。它不会读取 `plan.json`，也不识别假期、调课或学期边界，需人工维护。不要与 `autoplan` 同时运行。

`qwat.sign` 也会读取 `config.yaml` 中的 `state_file`、`photo` 和 `jitter_m`。`autoplan` 使用自己的命令行参数与计划文件，不读取该 YAML。

## 浏览器诊断与备用操作（可选）

已安装 `.[browser]` 和 Chromium 后：

```bash
python scripts/diagnose_page.py --head
python -m qwat.checkin --at 22.53 113.93 --head --dry-run
```

上面的坐标只是格式示例，请替换。浏览器演练只打开页面检查定位，不上传照片或点击提交。确需页面提交时使用 `--photo YOUR_PHOTO --yes`。备用流程依赖页面按钮文案和定位接口，页面更新后可能需要调整；截图会保存在本地。

## 检查仓库是否可运行

无需账号、无需网络的核心回归测试：

```bash
python -m pip check
python -m unittest discover -s tests -p "test_*.py" -v
```

安装浏览器辅助功能后，可额外运行本地假页面测试：

```bash
python tests/selftest_shim.py
python tests/selftest_browser.py
```

这些测试不会登录学校或提交考勤。真实账号和站点是否可用，请自己运行 `scripts/peek.py`、单次 `--dry-run` 及 `scripts/deploy_check.py` 验证。离线测试通过不代表学校接口始终可用。

## 目录与本地数据

```text
qwat/              登录、业务 API、签到、排程及可选浏览器辅助
scripts/           凭据保存、状态查询、课表导出、计划检查与部署诊断
tests/             离线回归测试和本地浏览器测试
deploy/            Linux systemd 模板和安装脚本
config.example.yaml  虚构的每周课表示例
pyproject.toml     Python 包信息、核心与可选依赖
```

`creds.bin`、`state.json`、`fingerprint.txt`、`config.yaml`、`plan.json`、`done.json`、照片、截图与导出的课表属于本地数据，默认已在 `.gitignore` 中排除。使用其他文件名存储资料时，要自行加入忽略规则。不要删除凭据、设备指纹或计划来“清缓存”，否则可能丢失登录能力、设备信任或执行状态。

许可证：[MIT](LICENSE)。
