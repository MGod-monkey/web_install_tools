"""Internal product configuration. There is deliberately no NAS settings UI."""

from dataclasses import dataclass, field
import os
from pathlib import Path

APP_NAME = "Robot Web Installer"
NAS_ORIGIN = "https://192.168.222.107:9443"
NAS_SHARE_ID = "71bc65396ae54836a3da6d69cc5d4189"
# Pin the NAS's self-signed certificate; do not disable TLS verification globally.
NAS_CERT_SHA256 = "e4f5d8195a04facd24c06d97f3d2445b83c4532cc5d97f973f98480661ef6ce1"
SERVICES = {"一代系统": "webservice.service", "二代系统": "nerva-web.service"}


def state_dir() -> Path:
    path = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "robot-web-installer"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def cache_dir() -> Path:
    path = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "robot-web-installer"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


@dataclass(frozen=True)
class RobotConfig:
    host: str = "192.168.121.1"
    username: str = "langyi"
    password: str = field(default="langyi", repr=False)
    port: int = 22
    generation: str = "一代系统"
    sudo_password: str = field(default="", repr=False)

    @property
    def service(self) -> str:
        return SERVICES[self.generation]

    def validate(self):
        if not self.host.strip() or self.host.startswith("-") or any(c.isspace() for c in self.host):
            raise ValueError("请输入有效的机器人 IP 或主机名")
        if not self.username.strip() or not 1 <= self.port <= 65535:
            raise ValueError("用户名或 SSH 端口无效")
        if self.generation not in SERVICES:
            raise ValueError("请选择系统代次")
        if "\n" in self.password or "\n" in self.sudo_password:
            raise ValueError("密码不能包含换行符")
