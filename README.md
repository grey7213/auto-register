# auto-register（账号注册机）

自动注册账号并提取订阅链接的工具。

> **当前公开 Release 为 Linux 版本。**  
> Windows 版本后续会单独打包上传到 [Releases](../../releases)。

## 平台说明

| 平台 | 状态 | Release 资源 |
|------|------|----------------|
| **Linux** | ✅ 当前版本 | `auto-register-linux-*.tar.gz` |
| Windows | ⏳ 计划中 | 后续由维护者在本机 Windows 上打包上传 |

## 功能

- 从站点白名单中**随机选择邮箱域名**
- 生成多样化邮箱本地名与密码（避免 `qa-xxx@qq.com` 同质化）
- 每次请求轮换 User-Agent / 客户端头（不含会触发 Cloudflare 拦截的 `CF-Connecting-IP`）
- 注册成功后自动提取 **订阅链接**
- GUI 双击启动 / 命令行批量注册

## Linux 环境要求

- Python 3.10+（建议 3.11/3.12）
- `python3-tk`（GUI 需要）
- `requests`

Debian / Kali / Ubuntu 示例：

```bash
sudo apt update
sudo apt install -y python3 python3-tk python3-requests
# 或使用 pip：
# pip3 install -r requirements.txt
```

## 快速开始（Linux）

### 1. 从 Release 下载

1. 打开仓库 [Releases](../../releases)
2. 下载 **Linux** 资源：`auto-register-linux-vX.Y.Z.tar.gz`
3. 解压：

```bash
tar -xzf auto-register-linux-vX.Y.Z.tar.gz
cd auto-register-linux-vX.Y.Z
```

### 2. 图形界面

```bash
python3 register_gui.py
# 或
./一键注册.sh
```

桌面快捷方式：可将 `一键注册.desktop` 复制到 `~/Desktop/`，把其中的路径改成你的解压目录后双击运行。

### 3. 命令行

```bash
# 注册 1 个账号并写入 accounts.jsonl
python3 register_account.py --count 1 --output accounts.jsonl

# 批量 5 个，间隔 1 秒
python3 register_account.py --count 5 --delay 1 --output accounts.jsonl

# 固定邮箱域名（本地名仍随机）
python3 register_account.py --count 3 --email-domain gmail.com --output accounts.jsonl
```

成功结果会追加写入 `accounts.jsonl`（**请勿提交到 Git**，已在 `.gitignore` 中忽略）。

## 目录结构（Linux 包）

```
register_account.py   # 核心注册逻辑（CLI）
register_gui.py       # 图形界面
一键注册.sh           # 启动脚本
一键注册.desktop      # 桌面快捷方式模板
requirements.txt      # Python 依赖
README.md
```

## 安全说明

- 本工具仅用于你有权测试的目标环境。
- `accounts.jsonl` 含邮箱/密码/订阅链接，请妥善保管，不要上传公开仓库。
- 仓库与 Release **不包含**任何已注册账号数据。

## 从源码运行

```bash
git clone https://github.com/grey7213/auto-register.git
cd auto-register
pip3 install -r requirements.txt
python3 register_gui.py
```

## 版本与后续

- **Linux**：见最新 Release 中的 `auto-register-linux-*.tar.gz`
- **Windows**：将以 `auto-register-windows-*.zip`（或类似命名）单独发布，不会覆盖 Linux 资源

## License

仅供个人/授权测试使用。
