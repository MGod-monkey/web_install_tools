from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class DebPackage:
    path: Path
    name: str
    version: str
    architecture: str
    depends: str
    installed_kib: int
    size: int
    digest: str

    @classmethod
    def inspect(cls, filename) -> "DebPackage":
        path = Path(filename).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() != ".deb":
            raise ValueError("请选择一个有效的 .deb 文件")
        env = None
        if getattr(sys, "frozen", False):
            # The system dpkg-deb needs the host libraries rather than PyInstaller's bundle.
            env = os.environ.copy()
            original = env.pop("LD_LIBRARY_PATH_ORIG", None)
            env.pop("LD_LIBRARY_PATH", None)
            if original is not None:
                env["LD_LIBRARY_PATH"] = original
        try:
            result = subprocess.run(
                ["dpkg-deb", "--field", str(path)],
                capture_output=True,
                text=True,
                errors="replace",
                env=env,
                timeout=30,
                check=True,
            )
        except FileNotFoundError as exc:
            raise ValueError("无法读取 DEB：系统找不到 dpkg-deb") from exc
        except subprocess.TimeoutExpired as exc:
            raise ValueError("无法读取 DEB：dpkg-deb 运行超时") from exc
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.strip() or f"dpkg-deb 退出码 {exc.returncode}"
            raise ValueError(f"无法读取 DEB：{detail}") from exc
        except OSError as exc:
            raise ValueError(f"无法运行 dpkg-deb：{exc.strerror}") from exc
        fields = {}
        last_key = ""
        for line in result.stdout.splitlines():
            if line.startswith((" ", "\t")):
                if last_key:
                    fields[last_key] += " " + line.strip()
            elif ":" in line:
                last_key, value = line.split(":", 1)
                fields[last_key] = value.strip()
        name = fields.get("Package", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", name):
            raise ValueError("DEB 的 Package 字段无效")
        if not fields.get("Version") or not fields.get("Architecture"):
            raise ValueError("DEB 缺少版本或 CPU 架构信息")
        return cls(
            path,
            name,
            fields["Version"],
            fields["Architecture"],
            fields.get("Depends", ""),
            int(fields.get("Installed-Size", "0")),
            path.stat().st_size,
            sha256(path),
        )

    def require_architecture(self, architecture: str):
        if self.architecture not in ("all", architecture):
            raise ValueError(f"架构不匹配：安装包是 {self.architecture}，机器人是 {architecture}")
