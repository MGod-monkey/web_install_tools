import base64
import codecs
from dataclasses import dataclass
import hashlib
from importlib.resources import files
import shlex
import socket
import time
import uuid

import paramiko

from .config import RobotConfig, state_dir
from .packages import DebPackage, sha256
from .storage import History


class HostKeyUnknown(RuntimeError):
    def __init__(self, host, key):
        self.host = host
        self.key = key
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        super().__init__(f"{host}\n{key.get_name()} SHA256:{fingerprint}")


class RemoteTaskFailed(RuntimeError):
    """The remote task has a definite failure, rather than an unknown outcome."""


class JournalDecoder:
    """Decode complete lines before redacting: UTF-8 and passwords may span SSH packets."""

    def __init__(self, redact, max_line=16384):
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.pending = ""
        self.discarding = False
        self.redact = redact
        self.max_line = max_line

    def feed(self, data, final=False):
        text = self.pending + self.decoder.decode(data, final=final)
        parts = text.split("\n")
        self.pending = parts.pop()
        lines = []
        for line in parts:
            if self.discarding:
                self.discarding = False
            elif len(line) > self.max_line:
                lines.append("[单行日志过长，已省略]")
            else:
                lines.append(self.redact(line).rstrip("\r"))
        if len(self.pending) > self.max_line:
            if not self.discarding:
                lines.append("[单行日志过长，已省略]")
            self.pending = ""
            self.discarding = True
        if final and self.pending and not self.discarding:
            lines.append(self.redact(self.pending))
            self.pending = ""
        return "\n".join(lines)


class VerifyHostKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        raise HostKeyUnknown(hostname, key)


def trust_host(error):
    path = state_dir() / "known_hosts"
    keys = paramiko.HostKeys()
    if path.exists():
        keys.load(str(path))
    keys.add(error.host, error.key.get_name(), error.key)
    keys.save(str(path))
    path.chmod(0o600)


@dataclass(frozen=True)
class RobotInfo:
    ubuntu: str
    architecture: str
    available_bytes: int
    service_state: str


class RobotClient:
    def __init__(self, config: RobotConfig):
        config.validate()
        self.config = config
        self.client = paramiko.SSHClient()
        self.client.load_system_host_keys()
        known = state_dir() / "known_hosts"
        if known.exists():
            self.client.load_host_keys(str(known))
        self.client.set_missing_host_key_policy(VerifyHostKey())

    def connect(self):
        try:
            self.client.connect(
                hostname=self.config.host,
                port=self.config.port,
                username=self.config.username,
                password=self.config.password,
                timeout=7,
                banner_timeout=7,
                auth_timeout=10,
                channel_timeout=30,
                allow_agent=False,
                look_for_keys=False,
            )
        except paramiko.AuthenticationException as exc:
            raise RuntimeError("机器人认证失败，请检查用户名和密码") from exc
        except paramiko.BadHostKeyException as exc:
            raise RuntimeError("机器人 SSH 身份与已保存记录不一致，请先核实设备身份") from exc
        self.client.get_transport().set_keepalive(15)
        return self

    def close(self):
        self.client.close()

    def interrupt(self):
        """Unblock a cancelled reader without waiting for network timeouts in the GUI thread."""
        transport = self.client.get_transport()
        if transport is not None:
            try:
                transport.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def follow_service_logs(self, stop_event, emit, connected=lambda: None, lines=200):
        if not isinstance(lines, int) or not 1 <= lines <= 5000:
            raise ValueError("历史日志行数必须在 1–5000 之间")
        if stop_event.is_set():
            return
        service = self.config.service
        # Verify access and unit existence; do not start, stop or modify the service.
        _, load_state, _ = self.run(
            "systemctl show --property=LoadState --value " + shlex.quote(service),
            sudo=True,
        )
        if stop_event.is_set():
            return
        if load_state.strip() == "not-found" or not load_state.strip():
            raise RuntimeError(f"机器人上未找到 {self.config.generation}服务 {service}")
        journal = f"journalctl --no-pager --output=short-iso --lines={lines} --follow --unit=" + shlex.quote(
            service
        )
        # Own only this reader process. EOF from a disconnected SSH session cleans it up,
        # including when the service is idle and journalctl would otherwise never write.
        script = (
            journal + " &\njournal_pid=$!\n"
            'trap \'kill "$journal_pid" 2>/dev/null || true; '
            'wait "$journal_pid" 2>/dev/null || true\' EXIT\n'
            "trap 'exit 0' HUP TERM\n"
            'while kill -0 "$journal_pid" 2>/dev/null; do\n'
            "  if IFS= read -r -t 1 control; then\n"
            '    if [ "$control" = __STOP_ROBOT_LOGS__ ]; then exit 0; fi\n'
            '  else\n    result=$?\n    if [ "$result" -eq 1 ]; then exit 0; fi\n'
            '  fi\ndone\nwait "$journal_pid"'
        )
        command = "sudo -S -p '' -- /bin/bash -c " + shlex.quote(script)
        stdin, stdout, stderr = self.client.exec_command(command, timeout=15)
        channel = stdout.channel
        output_decoder = JournalDecoder(self.redact)
        error_decoder = JournalDecoder(self.redact)
        errors = ""
        try:
            stdin.write((self.config.sudo_password or self.config.password) + "\n")
            stdin.flush()
            connected()
            while not stop_event.is_set():
                if channel.recv_ready():
                    text = output_decoder.feed(channel.recv(65536))
                    if text:
                        emit(text)
                if channel.recv_stderr_ready():
                    text = error_decoder.feed(channel.recv_stderr(65536))
                    if text:
                        errors = (errors + "\n" + text)[-4000:]
                        emit("[日志读取提示] " + text)
                if (
                    channel.exit_status_ready()
                    and not channel.recv_ready()
                    and not channel.recv_stderr_ready()
                ):
                    for decoder in (output_decoder, error_decoder):
                        tail = decoder.feed(b"", final=True)
                        if tail:
                            emit(tail)
                    if stop_event.is_set():
                        return
                    code = channel.recv_exit_status()
                    if code:
                        raise RuntimeError(f"服务日志读取失败（{code}）。{errors}")
                    raise RuntimeError("日志连接已结束，请点击“开始跟踪”重新连接")
                if not self.client.get_transport().is_active():
                    raise RuntimeError("机器人日志连接已断开，请检查网络后重新连接")
                stop_event.wait(0.05)
        finally:
            try:
                stdin.write("__STOP_ROBOT_LOGS__\n")
                stdin.flush()
                channel.shutdown_write()
            except (OSError, EOFError):
                pass
            channel.close()

    def run(self, command, *, sudo=False, timeout=40, check=True):
        if sudo:
            command = "sudo -S -p '' -- /bin/bash -c " + shlex.quote(command)
        stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        if sudo:
            stdin.write((self.config.sudo_password or self.config.password) + "\n")
            stdin.flush()
        stdin.channel.shutdown_write()
        channel = stdout.channel
        out, err = bytearray(), bytearray()
        deadline = time.monotonic() + timeout
        while True:
            if channel.recv_ready():
                out.extend(channel.recv(65536))
            if channel.recv_stderr_ready():
                err.extend(channel.recv_stderr(65536))
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            if time.monotonic() >= deadline:
                channel.close()
                raise TimeoutError("机器人命令超时；若已启动部署，请使用“恢复任务”查询")
            time.sleep(0.02)
        code = channel.recv_exit_status()
        output = out.decode("utf-8", errors="replace")
        error = err.decode("utf-8", errors="replace")
        if check and code:
            if sudo and ("password" in error.lower() or "sudoers" in error.lower()):
                raise RuntimeError("sudo 认证失败或权限不足；请检查高级连接选项")
            raise RuntimeError((error or output or f"远程命令失败：{code}")[-4000:])
        return code, output, error

    def inspect(self):
        command = (
            '. /etc/os-release; printf \'%s %s\\n\' "$ID" "$VERSION_ID"; '
            "dpkg --print-architecture; df -PB1 /var /tmp | awk 'NR>1 {print $4}'"
        )
        _, text, _ = self.run(command)
        lines = text.strip().splitlines()
        if len(lines) != 4 or lines[0] not in ("ubuntu 20.04", "ubuntu 22.04", "ubuntu 24.04"):
            raise RuntimeError("机器人必须运行 Ubuntu 20.04、22.04 或 24.04")
        self.run("true", sudo=True)
        _, service_state, _ = self.run(
            "systemctl is-active " + shlex.quote(self.config.service),
            check=False,
        )
        return RobotInfo(
            lines[0],
            lines[1],
            min(int(lines[2]), int(lines[3])),
            service_state.strip() or "unknown",
        )

    def deploy(self, package, log, progress):
        log("正在检查机器人环境及 sudo 权限…")
        info = self.inspect()
        if DebPackage.from_path(package.path) != package:
            raise RuntimeError("本地安装包在选择后发生变化，请重新选择")
        if info.available_bytes < max(package.size * 3, 256 * 1024**2):
            raise RuntimeError("机器人 /var 或 /tmp 剩余空间不足")
        try:
            digest = sha256(package.path)
        except OSError as exc:
            raise ValueError(f"无法读取本地文件：{package.path.name}") from exc
        task_id = uuid.uuid4().hex
        history = History()
        history.create(task_id, self.config, package)
        stage_path = "/tmp/robot-web-installer-" + task_id
        root = "/var/lib/robot-web-installer/tasks/" + task_id
        started = False
        try:
            self.run("mkdir -m 700 -- " + shlex.quote(stage_path))
            progress(10)
            log("通过 SFTP 上传安装包…")
            with self.client.open_sftp() as sftp:
                sftp.get_channel().settimeout(45)
                sftp.put(
                    str(package.path),
                    stage_path + "/package.deb",
                    callback=lambda done, total: progress(10 + int(done * 20 / max(total, 1))),
                )
                script = files("robot_installer").joinpath("remote/install.sh").read_bytes()
                with sftp.open(stage_path + "/install.sh", "wb") as target:
                    target.write(script.replace(b"\r\n", b"\n"))
            _, output, _ = self.run("sha256sum -- " + shlex.quote(stage_path + "/package.deb"), timeout=120)
            if output.split()[:1] != [digest]:
                raise RuntimeError("机器人上的文件校验失败，未执行安装")
            log("文件校验通过，启动远程部署任务：" + task_id)
            command = (
                "install -d -m 755 /var/lib/robot-web-installer/tasks "
                + shlex.quote(root)
                + " && install -m 600 "
                + shlex.quote(stage_path + "/package.deb")
                + " "
                + shlex.quote(root + "/package.deb")
                + " && install -m 700 "
                + shlex.quote(stage_path + "/install.sh")
                + " "
                + shlex.quote(root + "/install.sh")
                + " && systemd-run --quiet --unit=robot-web-deploy-"
                + task_id
                + " --property=Type=exec /bin/bash "
                + " ".join(
                    shlex.quote(arg)
                    for arg in (
                        root + "/install.sh",
                        root,
                        self.config.service,
                        digest,
                    )
                )
            )
            # If the reply is lost, assume the task may have started and allow recovery.
            history.update(task_id, "执行中")
            started = True
            code, output, error = self.run(command, sudo=True, timeout=180, check=False)
            if code:
                message = "远程任务启动失败：" + self.redact((error or output)[-2000:])
                history.update(task_id, "失败", message)
                raise RemoteTaskFailed(message)
            return self.monitor(task_id, log, progress)
        except RemoteTaskFailed:
            raise
        except Exception as exc:
            if started:
                history.update(task_id, "待查询", self.redact(str(exc)))
                raise RuntimeError(
                    f"任务 {task_id} 需要查询结果，请点击“恢复任务”。\n{self.redact(str(exc))}"
                ) from exc
            history.update(task_id, "失败", self.redact(str(exc)))
            raise
        finally:
            try:
                self.run("rm -rf -- " + shlex.quote(stage_path), timeout=10, check=False)
            except Exception:
                pass

    def redact(self, text):
        for secret in (self.config.password, self.config.sudo_password):
            if secret:
                text = text.replace(secret, "***")
        return text

    def record_package_metadata(self, sftp, root, history, task_id):
        try:
            with sftp.open(root + "/metadata", "rb") as remote:
                data = remote.read(4096).decode("utf-8", errors="replace")
        except (OSError, paramiko.SSHException):
            return
        fields = dict(line.split("=", 1) for line in data.splitlines() if "=" in line)
        if fields.get("package") and fields.get("version"):
            history.set_package(task_id, fields["package"], fields["version"])

    def monitor(self, task_id, log, progress, timeout=1800):
        if len(task_id) != 32 or any(c not in "0123456789abcdef" for c in task_id):
            raise ValueError("任务编号无效")
        root = "/var/lib/robot-web-installer/tasks/" + task_id
        offset = 0
        missing_since = time.monotonic()
        deadline = time.monotonic() + timeout
        history = History()
        with self.client.open_sftp() as sftp:
            sftp.get_channel().settimeout(45)
            while time.monotonic() < deadline:
                try:
                    with sftp.open(root + "/deploy.log", "rb") as remote:
                        remote.seek(offset)
                        data = remote.read(1024 * 256)
                        offset += len(data)
                        if data:
                            log(self.redact(data.decode("utf-8", errors="replace").rstrip()))
                    with sftp.open(root + "/status", "rb") as remote:
                        status = remote.read(256).decode().strip()
                except FileNotFoundError:
                    if time.monotonic() - missing_since > 12:
                        raise RuntimeError("未找到远程任务状态；任务可能未启动或机器人已重启，请核查部署记录")
                    time.sleep(1)
                    continue
                missing_since = time.monotonic()
                if status == "succeeded" or status.startswith("failed:"):
                    self.record_package_metadata(sftp, root, history, task_id)
                if status == "succeeded":
                    history.update(task_id, "成功", "服务已自启并持续运行 10 秒")
                    progress(100)
                    return task_id
                if status.startswith("failed:"):
                    stage = status.split(":")[1]
                    message = {
                        "preflight": "预检查失败",
                        "dependencies": "依赖或包冲突检查失败",
                        "install": "DEB 安装失败",
                        "service": "DEB 已安装，服务配置或启动失败",
                        "busy": "另一个部署任务正在执行",
                    }.get(stage, "部署失败")
                    history.update(task_id, "失败", message)
                    raise RemoteTaskFailed(message + "，请查看日志")
                progress(
                    {
                        "running:preflight": 35,
                        "running:dependencies": 40,
                        "running:install": 60,
                        "running:service": 85,
                    }.get(status, 35)
                )
                time.sleep(1)
        history.update(task_id, "待查询", "等待超过 30 分钟，请恢复查询")
        raise TimeoutError("等待超过 30 分钟，远程任务可能仍在执行，请稍后恢复查询")


def friendly_error(error):
    if isinstance(error, HostKeyUnknown):
        return str(error)
    if isinstance(error, (socket.timeout, TimeoutError)):
        return "连接或任务等待超时；如已开始安装，请恢复任务查询结果"
    if isinstance(error, (OSError, paramiko.SSHException)):
        return "SSH 连接中断或不可达，请检查机器人网络；如已开始安装，请恢复任务"
    return str(error)
