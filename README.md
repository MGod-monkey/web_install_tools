# 机器人 Web 部署工具

Ubuntu 桌面 AppImage。通过 NAS 选择 DEB 或拖入本地 DEB，经 SSH/SFTP 安装到机器人，为目标服务设置开机自启、重启并观察 10 秒运行状态。

## 使用

```bash
chmod +x RobotWebInstaller-0.3.0-x86_64.AppImage
./RobotWebInstaller-0.3.0-x86_64.AppImage
```

1. 填写机器人 IP、用户名、密码，选择系统代次。默认 IP 为 `192.168.121.1`，账号和密码均为 `langyi`。
2. 默认 SSH 端口 22；高级连接选项可填写其他端口与独立 sudo 密码。
3. 在 NAS 目录树选中 DEB，或在路径栏输入完整 DEB 路径并按回车。输入目录路径可展开目录，例如 `/web_deb/第一代系统/temp/`。本地 DEB 可拖入窗口，也可点击拖拽区选择。
4. 点击“一键部署”，选中的 NAS 包会按需下载并上传到机器人，由机器人读取和安装。首次连接时核实机器人的 SSH 指纹，信任后保存；已保存的主机身份变化时拒绝连接。
5. 等待结果。支持部署记录、日志导出和断连后恢复查询。

| 系统 | 目标服务 |
| --- | --- |
| 一代系统 | `webservice.service` |
| 二代系统 | `nerva-web.service` |

代次仅改变工具操作的服务，不过滤目录，不修改 DEB 内部脚本。服务文件由 DEB 提供或在机器人上预装。

NAS 配置固定在内部，没有前端设置入口。采用已验证的绿联 UGOS 只读分享接口，不保存 NAS 账号密码。自签名证书通过 SHA-256 指纹验证。分享失效、增加提取密码或更换证书时，由维护人员更新内部配置。

## 网页快捷入口

主页四个按钮使用当前填写的机器人 IP，通过系统默认浏览器打开，无需先测试 SSH 或选择 DEB：

| 按钮 | 地址 |
| --- | --- |
| 一代网页 | `http://机器人IP:8080` |
| 二代网页 | `http://机器人IP:3003` |
| 一代后端 | `http://机器人IP:8899/docs` |
| 二代后端 | `http://机器人IP:3003/docs` |

四个入口始终可用，不受系统代次选择限制。地址框只填写 IP 或主机名，不填写协议、端口和路径。

## SSH 远程终端

主页点击“远程终端”，使用当前机器人连接信息打开独立的交互式 SSH 终端。它通过远端 PTY 运行登录 shell，可以直接输入命令，支持 `sudo` 密码输入、方向键历史、Tab 补全、Ctrl+C 中断、Ctrl+D 退出、ANSI 彩色输出、中文输入和 `vi` / `top` 等全屏终端程序。

- 支持单屏、左右二分屏、左一右二的三分屏、二乘二四分屏，可拖动分隔线调整大小。缩放会同步更新远端 PTY 尺寸。
- 每个窗格是独立 SSH 连接，分别保留当前目录、环境变量与前台程序；通过各窗格的“设置”可连接不同机器人、端口或账号。
- 点击“添加分屏”会打开一个新的 SSH 会话，最多同时显示四个窗格。多窗格时可关闭任意一个分屏，其余窗格的会话保持连接。
- 各窗格可单独连接或断开。远程终端、部署和实时服务日志可同时使用。
- 鼠标拖选后按 Ctrl+Shift+C 复制，Ctrl+Shift+V 或 Shift+Insert 粘贴。Ctrl+C 始终发送给远端，不用于本地复制。
- 普通屏幕可用滚轮或 Shift+PageUp/PageDown 查看回滚。支持应用鼠标协议；应用捕获鼠标时按住 Shift 可拖选文本。
- 支持常用终端颜色、替代屏幕、光标控制与括号粘贴，回滚上限 3000 行；不提供 SSH 端口转发、图形转发或文件传输面板。

终端采用与部署相同的 SSH 主机指纹校验，账号密码仅保存在当前窗口内存中，不记录终端输入或输出到应用日志文件。终端中手动输入的命令正常在机器人执行，机器人 shell 可能按自身设置保存命令历史。关闭窗口或断开会话会结束 SSH 连接，前台程序可能随会话退出；需要长时间后台运行时可使用机器人自身的 tmux、screen 或 systemd。

## 实时服务日志

填写机器人连接信息并选择系统代次后，点击“查看服务日志”，即可通过独立 SSH 连接打开实时日志窗口，无需先选择或安装 DEB。

- 一代系统读取 `webservice.service`，二代系统读取 `nerva-web.service`。
- 先显示该服务最近 200 行日志，再用 `journalctl --follow` 持续显示新日志，包括服务重启后的输出。
- 窗口明确显示机器人 IP、系统代次和服务名称。打开时固定连接目标；修改主窗口的配置不会改变已打开窗口。可以分别打开两个窗口对照查看。
- 可停止、重新开始跟踪、清空窗口、开关自动滚动、导出当前窗口内容。窗口保留最近 5000 行，导出也限于这些内容。
- 查看日志使用独立线程，不阻塞部署。停止跟踪或关闭日志窗口仅断开日志连接，不停止、重启或修改机器人服务。关闭软件会先断开日志连接。
- 网络断开或权限不足时显示原因，恢复网络后点击“开始跟踪”重新连接；重新连接会再读取最近 200 行。
- 复用 SSH 主机身份校验和 sudo 密码配置；日志按完整行解码并对当前连接密码脱敏，不自动写入部署日志文件。

需要机器人允许该账号通过 sudo 读取日志。显示的是 systemd journal 收集到的服务标准输出与错误输出；如果业务自行重定向到文件，则这些文件中的内容不会自动出现在此窗口。没有日志时连接会保持等待，不会因服务暂时没有输出而判定失败。超长单行会提示并省略，以限制内存占用。

## 离线部署

- 电脑先访问 NAS 下载，再经 SFTP 上传给机器人，机器人无需访问 NAS。
- 可以先下载，再切换到机器人网络。重新打开工具后，可通过文件选择器从下载缓存目录选择之前保存的 DEB。
- 无 NAS、无互联网时仍能选择本地包，但电脑必须能通过 SSH 连接机器人。
- AppImage 包含 Python、Qt、SSH/HTTPS 库及中文字体，不需要安装 Python 或 Node.js。
- 机器人必须已安装 DEB 声明的依赖。工具使用独立的空软件源和索引，仅解析本地包与已安装依赖；依赖缺失时在安装前报错，不自动修复系统、删除包或全量升级。
- DEB 自己的维护脚本可能访问网络或修改其他服务，这部分由业务包决定。

缺少 FUSE 时可解包运行：

```bash
./RobotWebInstaller-0.3.0-x86_64.AppImage --appimage-extract-and-run
```

## 执行流程

1. 检查机器人的 Ubuntu 版本、上传所需空间和 sudo 权限。电脑端只读取文件及传输所需信息。
2. SFTP 上传到独立临时目录，校验两端 SHA-256。
3. 复制到 root 管理的任务目录，通过 systemd 临时服务启动任务。
4. 在机器人端读取 DEB 信息，检查架构、可用空间和 DPKG 状态，并阻止降级。用 `flock` 保证部署互斥，记录原版本和服务状态，备份包管理器声明的 `/etc` 配置。
5. APT 模拟安装；通过后实际安装，禁止删除其他包并保留已有配置。
6. `daemon-reload`、`enable`、`restart`，检查连续 10 秒运行状态及开机自启状态。
7. 保存任务状态与日志，分别报告安装失败和服务失败，服务失败时收集 journal 日志。

安装任务独立于 SSH 运行。掉线后重新打开软件、输入原机器人的密码，点击“恢复任务”。默认恢复最近的待查询任务，也可在部署记录中选择任务。结果未知时先恢复查询，避免重复安装。

成功表示安装完成、目标服务已自启且通过运行状态观察，不代表所有业务接口都已检查。机器人重启后的自启效果仍需在测试设备验证。

支持同版本重装，当前禁止降级。备份限于包管理器声明的配置，不包含任意业务目录或数据库，不提供自动回滚。失败后保留远程日志与包供维护，成功后删除远程上传的 DEB。

## 数据位置

- 状态、SSH 指纹、SQLite 历史和脱敏日志：`~/.local/state/robot-web-installer/`。
- 下载缓存：`~/.cache/robot-web-installer/`。
- 机器人任务：`/var/lib/robot-web-installer/tasks/<任务编号>/`。
- 临时服务：`robot-web-deploy-<任务编号>.service`。

不持久化登录密码或 sudo 密码。当前连接密码从应用日志中脱敏，配置备份仅机器人 root 可读。

## 开发与测试

### Qt 主题

深色主题位于 [style.qss](src/robot_installer/assets/style.qss)。应用启动时从包资源加载，可在其他 PySide 应用中用相同方式复用。

```python
from importlib.resources import files

theme = files("robot_installer").joinpath("assets", "style.qss")
app.setStyleSheet(theme.read_text(encoding="utf-8"))
```

| 组件 | QSS 选择器所需设置 |
| --- | --- |
| 连接、NAS 和日志面板 | `QFrame.setObjectName("card")` |
| 拖拽区、当前安装包、NAS 目录、日志进度条 | 分别使用 `dropZone`、`packageSelection`、`nasTree`、`logProgress` 作为 `objectName` |
| 标题、状态和服务文本 | `QLabel` 的 `objectName` 为 `title`、`section`、`muted` 或 `service` |
| 终端窗格、视口、目标和状态 | 分别使用 `terminalPane`、`terminalViewport`、`terminalTarget`、`terminalStatus` 作为 `objectName` |
| 主要操作、强调操作、关闭分屏 | 对按钮调用 `setProperty("variant", …)`，值分别为 `primary`、`accent`、`close` |

字号通过 QSS 设置。控件的行高和间距由最小高度、内边距和 Qt 布局共同控制。启动入口还为 Fusion 原生控件设置深色调色板。

### 本地运行

```bash
/usr/bin/python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
env -u PYTHONPATH .venv/bin/python -m robot_installer

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ROBOT_DOCKER_TESTS=1 .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests run.py
```

Docker 测试仅在无网络的一次性容器内安装合成包，不连接真实机器人。SSH 测试使用本机回环服务器，覆盖主机身份验证、密码通过标准输入传递、大量标准输出与错误输出。容器中 systemd 命令替换为记录器，校验代次映射和失败处理，不能替代真实系统启动验收。

交互式终端集成测试使用一次性 OpenSSH 容器，验证真实 PTY、命令执行、Ctrl+C、中文 vi 编辑、尺寸更新和四个独立 shell。测试账号仅存在于该容器：

```bash
docker build -f tests/fixtures/ssh.Dockerfile -t robot-web-installer:ssh-test .
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ROBOT_TERMINAL_TESTS=1 .venv/bin/python -m pytest -q
```

## 构建

在 Ubuntu 上构建 Windows EXE 和 Linux AppImage，需要 Docker、Python 3、Git 和已登录的 GitHub CLI。提交要构建的代码和 Windows 工作流后，在仓库根目录运行以下命令。

```bash
./build.sh -v 0.3.1
```

脚本从当前提交建立独立构建目录，设置版本号并单独推送 `v0.3.1` 标签。GitHub Actions 构建并测试 Windows EXE，然后建立 Releases 草稿。本机用 Docker 构建并测试同一标签的 AppImage，保存到 `dist/v0.3.1/`，再上传到该草稿。再次运行同一版本会使用已有标签，补齐缺少的附件。

单独在本机打包 Linux AppImage 时，可运行以下命令。

```bash
bash scripts/build_in_docker.sh
```

使用 Python 3.11 / Debian 11（glibc 2.31）基线，面向 Ubuntu 20.04、22.04、24.04 的 x86_64 桌面。业务包架构单独根据机器人检测；ARM 桌面需要另构建 ARM AppImage。

Debian 11 安全仓库已归档，构建使用官方 archive 地址。Qt 和主要依赖固定版本；升级后需重新验证最低 glibc 要求。构建 runtime 会检查 `packaging/runtime.sha256`，上游 continuous 产物变化时停止，需维护人员核实后更新。

产物位于 `dist/`，包括 AppImage 和 SHA-256 文件。验证打包后的 GUI 与运行时：

```bash
QT_QPA_PLATFORM=offscreen ./dist/RobotWebInstaller-0.3.0-x86_64.AppImage \
  --appimage-extract-and-run --smoke-test
```

Noto Sans CJK 和 DejaVu Sans Mono 字体许可随产物附带。Qt/PySide6、pyte 等组件许可随产物保存，分发时需保留。

## 验收边界

完整发布还需实际 Ubuntu 桌面拖拽与文件对话框测试、一代和二代测试机器人安装、实际服务输出的日志跟踪、安装中断连后恢复，以及机器人重启自启验证。容器检查和回环 SSH 流式测试不替代硬件和桌面验收。

真实 NAS 已完成目录浏览和现有 DEB 下载验证。当前现有包内部版本是 `webservice 5.4.4`，其脚本包含一代服务配置；二代目录目前为空，不能仅更改目标服务就将该包视作二代兼容包。
