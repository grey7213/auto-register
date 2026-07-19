# auto-register

自动注册账号、提取订阅，并在 **Windows** 上监控 Clash Verge 2GB 试用订阅、用完自动切换 / 自动注册。

## 平台

| 平台 | 状态 | Release 资源 |
|------|------|----------------|
| **Linux** | ✅ | `auto-register-linux-*.tar.gz` |
| **Windows** | ✅ | `auto-register-windows-*.zip` |

## 功能

### 注册机（跨平台）

- 从站点白名单中**随机选择邮箱域名**
- 生成多样化邮箱本地名与密码（避免 `qa-xxx@qq.com` 同质化）
- 每次请求轮换 User-Agent / 客户端 IP 头（**不含**会触发 Cloudflare 的 `CF-Connecting-IP`）
- 注册成功后自动提取 **订阅链接**
- GUI / 命令行均可

### Windows 额外：Clash Verge 2G 自动切换

- 排除主订阅（默认 `ilovesushi.cc` / ≥50GB）
- 监控 1～5GB 试用订阅流量
- 用到阈值（默认 95% 或剩余 &lt;50MB）自动切下一个
- 试用池用尽后自动注册新号并导入切换
- 通过命名管道 `\\.\pipe\verge-mihomo` 热加载（不修改内核 exe）

## 环境要求

- Python 3.10+
- `pip install -r requirements.txt`
- GUI 需要 tkinter（Windows 官方安装包通常自带；Linux 需 `python3-tk`）

```bash
pip install -r requirements.txt
```

## Windows 快速开始

### 从 Release 下载

1. 打开 [Releases](../../releases)
2. 下载 `auto-register-windows-vX.Y.Z.zip`
3. 解压后双击：

| 文件 | 作用 |
|------|------|
| `打开注册机GUI.bat` | 图形界面注册 |
| `一键注册并提取订阅.bat` | 注册 1 个号并导出 Clash YAML |
| `自动切换-干跑检测.bat` | 只检测 2G 流量 |
| `自动切换-检测一次.bat` | 检测一次并切换/注册 |
| `自动切换-常驻监控.bat` | 每 120 秒常驻监控（关窗口即停） |

也可先运行 `python make_launchers.py` 按本机 Python 路径重写 bat。

### 命令行

```bat
:: 注册（随机邮箱 + 随机客户端头）
python register_account.py --count 1 --output accounts.jsonl

:: 批量 5 个
python register_account.py --count 5 --delay 1 --output accounts.jsonl

:: 2G 自动切换（需 Clash Verge 正在运行）
python auto_switch.py --once --dry-run
python auto_switch.py --interval 120
```

## Linux 快速开始

```bash
tar -xzf auto-register-linux-vX.Y.Z.tar.gz
cd auto-register-linux-vX.Y.Z
python3 register_gui.py
# 或
./一键注册.sh
```

```bash
python3 register_account.py --count 1 --output accounts.jsonl
```

## 目录结构

```
register_account.py      # 注册核心（随机邮箱/IP）
register_gui.py          # 图形界面
auto_switch.py           # Windows Clash Verge 2G 自动切换
mihomo_controller.py     # Windows 命名管道 / Unix socket 控制器
rotate_subscription.py   # 登录、拉订阅、写备份
export_subscription.py   # 导出 Clash YAML 到本地文件夹
make_launchers.py        # 生成 Windows .bat
一键注册.sh / .desktop   # Linux 启动
requirements.txt
```

## 安全说明

- 仅用于你有权测试的目标环境。
- `accounts.jsonl` 含邮箱/密码/订阅链接，**请勿提交到 Git**（已在 `.gitignore`）。
- 仓库与 Release **不包含**任何已注册账号数据。
- 自动切换只改 `profiles.yaml` 当前选中项与订阅文件，**不修改** Clash 内核可执行文件。

## License

仅供个人/授权测试使用。
