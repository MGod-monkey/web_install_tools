from dataclasses import dataclass
import hashlib
from pathlib import Path
import stat


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class DebPackage:
    path: Path
    size: int
    modified_ns: int

    @classmethod
    def from_path(cls, filename) -> "DebPackage":
        path = Path(filename).expanduser().absolute()
        if path.suffix.lower() != ".deb":
            raise ValueError("请选择一个 .deb 文件")
        try:
            info = path.stat()
        except OSError as exc:
            raise ValueError(f"无法访问本地文件：{path.name}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("请选择一个 .deb 文件")
        return cls(path, info.st_size, info.st_mtime_ns)
