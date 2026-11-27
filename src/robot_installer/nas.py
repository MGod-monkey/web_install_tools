"""Read-only UGOS share API, verified against this NAS's own web client."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
import threading

import requests
from requests.adapters import HTTPAdapter

from .config import NAS_CERT_SHA256, NAS_ORIGIN, NAS_SHARE_ID, cache_dir


class NasError(RuntimeError):
    pass


class NasPathError(ValueError):
    pass


SHARE_PATH = PurePosixPath("/web_deb")


class PinnedAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        kwargs.update(assert_fingerprint=NAS_CERT_SHA256, cert_reqs="CERT_NONE")
        return super().init_poolmanager(connections, maxsize, block, **kwargs)

    def cert_verify(self, conn, url, verify, cert):
        # urllib3 verifies the exact SHA256 fingerprint on the same TLS connection.
        conn.cert_reqs = "CERT_NONE"
        conn.ca_certs = None
        conn.ca_cert_dir = None


@dataclass(frozen=True)
class NasEntry:
    path: str
    name: str
    is_dir: bool
    size: int = 0
    mtime: int = 0


class NasClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.mount(NAS_ORIGIN + "/", PinnedAdapter())
        self.session.headers.update({"UG-Agent": "PC/WEB", "X-Specify-Language": "zh-CN"})
        self.session.headers.update(
            {
                "Origin": NAS_ORIGIN,
                "Referer": NAS_ORIGIN + "/filemgr/share-download/?id=" + NAS_SHARE_ID,
            }
        )
        self.root = ""
        self._lock = threading.Lock()

    def _request(self, method, endpoint, **kwargs):
        try:
            response = self.session.request(
                method,
                NAS_ORIGIN + "/ugreen/v1/filemgr/" + endpoint,
                timeout=(5, 30),
                allow_redirects=False,
                **kwargs,
            )
            response.raise_for_status()
            body = response.json()
        except requests.exceptions.SSLError as exc:
            raise NasError("NAS 证书已变化，请联系维护人员更新内置证书") from exc
        except (requests.RequestException, ValueError) as exc:
            raise NasError("无法访问 NAS，请检查局域网连接；也可使用本地 DEB") from exc
        if body.get("code") != 200:
            raise NasError(f"NAS 文件接口返回错误（{body.get('code', '未知')}）")
        return body.get("data", {})

    def connect(self):
        info = self._request("GET", "queryShareInfo", params={"share_id": NAS_SHARE_ID})
        if any(info.get(k) for k in ("expired", "visit_limit_reached", "file_not_exist")):
            raise NasError("NAS 分享已失效或文件不存在，请联系维护人员")
        if info.get("has_password"):
            raise NasError("NAS 分享新增了提取密码，请联系维护人员更新内部配置")
        if not info.get("has_download"):
            raise NasError("NAS 分享未开启下载权限")
        data = self._request(
            "POST",
            "externalVerifySharePassword",
            json={
                "share_id": NAS_SHARE_ID,
                "password": "",
                "no_count": True,
            },
        )
        for raw in data.get("file_info", []):
            if raw.get("file_type") == 1 and PurePosixPath(raw["path"]).name == SHARE_PATH.name:
                self.root = str(PurePosixPath(raw["path"]))
                return self.root
        raise NasError("分享中没有找到 web_deb 目录，请联系维护人员")

    def require_path(self, path):
        if not self.root:
            raise NasError("请先连接 NAS")
        candidate = PurePosixPath(path)
        if ".." in candidate.parts or not candidate.is_relative_to(PurePosixPath(self.root)):
            raise NasError("目录超出 web_deb 范围")

    def list_dir(self, path):
        self.require_path(path)
        entries = {}
        page = 1
        while True:
            data = self._request(
                "POST",
                "getShearDirFileList",
                json={
                    "token": "",
                    "share_id": NAS_SHARE_ID,
                    "password": "",
                    "path": path,
                    "page": page,
                    "limit": 1000,
                    "sort_type": 1,
                    "as_dir": False,
                    "reverse": False,
                    "file_exts": [],
                },
            )
            files = data.get("files", [])
            previous = len(entries)
            for raw in files:
                self.require_path(raw["path"])
                if str(PurePosixPath(raw["path"]).parent) != path:
                    raise NasError("NAS 返回了不属于当前目录的条目")
                entry = NasEntry(
                    raw["path"],
                    PurePosixPath(raw["path"]).name,
                    raw.get("file_type") == 1,
                    raw.get("size", 0),
                    raw.get("mtime", 0),
                )
                entries[entry.path] = entry
            if len(entries) >= int(data.get("total", len(entries))) or not files:
                break
            if len(entries) == previous or page >= 10000:
                raise NasError("NAS 目录分页异常，请刷新重试")
            page += 1
        return sorted(entries.values(), key=lambda e: (not e.is_dir, e.name.casefold()))

    def locate(self, path):
        """Return the root, the listings along the path and the target directory's contents."""
        path = path.strip()
        target = PurePosixPath(path)
        if not target.is_relative_to(SHARE_PATH) or ".." in target.parts or "\0" in path:
            raise NasPathError("请输入以 /web_deb 开头的 NAS 目录或 DEB 路径")
        if not self.root:
            self.connect()

        root = NasEntry(self.root, SHARE_PATH.name, True)
        entry = root
        ancestors = []
        for name in target.relative_to(SHARE_PATH).parts:
            if not entry.is_dir:
                raise NasPathError(f"路径中的「{entry.name}」不是目录")
            entries = self.list_dir(entry.path)
            child = next((candidate for candidate in entries if candidate.name == name), None)
            if child is None:
                raise NasPathError(f"找不到 NAS 路径：{target}")
            ancestors.append((entries, child))
            entry = child

        if entry.is_dir:
            return root, ancestors, self.list_dir(entry.path)
        if path.endswith("/") or not entry.name.lower().endswith(".deb"):
            raise NasPathError("请输入 NAS 目录或 .deb 文件路径")
        return root, ancestors, None

    def download(self, entry, progress=lambda *_: None):
        self.require_path(entry.path)
        if entry.is_dir or not entry.name.lower().endswith(".deb"):
            raise NasError("请选择 DEB 文件")
        with self._lock:
            task = self._request(
                "POST",
                "addPathsByShareId",
                json={
                    "paths": [entry.path],
                    "share_id": NAS_SHARE_ID,
                },
            ).get("result")
            if not task:
                raise NasError("NAS 未返回下载任务")
            folder = cache_dir() / hashlib.sha256(entry.path.encode()).hexdigest()[:24]
            folder.mkdir(exist_ok=True, mode=0o700)
            dest = folder / entry.name
            partial = folder / (entry.name + ".part")
            try:
                url = NAS_ORIGIN + "/ugreen/v1/filemgr/shareDownloadFile"
                with self.session.get(
                    url,
                    params={
                        "coding": "true",
                        "share_id": NAS_SHARE_ID,
                        "password": "",
                        "task_id": task,
                    },
                    stream=True,
                    timeout=(5, 60),
                    allow_redirects=False,
                ) as response:
                    response.raise_for_status()
                    if response.is_redirect or "json" in response.headers.get("Content-Type", ""):
                        raise NasError("NAS 下载被拒绝，请刷新目录后重试")
                    total = int(response.headers.get("Content-Length", "0"))
                    done = 0
                    with partial.open("wb") as output:
                        for block in response.iter_content(1024 * 1024):
                            output.write(block)
                            done += len(block)
                            progress(done, total or entry.size)
                if (total and done != total) or (entry.size and done != entry.size):
                    raise NasError("下载大小不匹配，文件未加入缓存")
                from .packages import DebPackage

                with TemporaryDirectory(prefix=".verify-", dir=folder) as staging:
                    candidate = Path(staging) / entry.name
                    partial.replace(candidate)
                    package = DebPackage.inspect(candidate)
                    package.path.replace(dest)
                (folder / "metadata.json").write_text(
                    json.dumps(
                        {
                            "path": entry.path,
                            "name": entry.name,
                            "sha256": package.digest,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                return dest
            except requests.RequestException as exc:
                raise NasError("NAS 下载中断，请检查局域网后重试") from exc
            finally:
                partial.unlink(missing_ok=True)


def cached_packages():
    return sorted(cache_dir().glob("*/*.deb"), key=lambda p: p.stat().st_mtime, reverse=True)
