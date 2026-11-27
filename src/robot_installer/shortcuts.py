"""Build robot HTTP shortcuts from a host, without accepting URLs or credentials."""

import ipaddress
import re
import os
import sys
from contextlib import contextmanager

from PySide6.QtCore import QUrl

WEB_SHORTCUTS = {
    "一代网页": (8080, ""),
    "二代网页": (3003, ""),
    "一代后端": (8899, "/docs"),
    "二代后端": (3003, "/docs"),
}


@contextmanager
def browser_environment():
    """Do not pass the AppImage's bundled library/plugin paths to a system browser."""
    if not getattr(sys, "frozen", False):
        yield
        return
    roots = [str(root) for root in (getattr(sys, "_MEIPASS", None), os.environ.get("APPDIR")) if root]

    def bundled(path):
        return any(path == root or path.startswith(root + os.sep) for root in roots)

    edits = {"LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH_ORIG")}
    for name in (
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QT_QPA_FONTDIR",
        "FONTCONFIG_FILE",
        "FONTCONFIG_PATH",
    ):
        if bundled(os.environ.get(name, "")):
            edits[name] = None
    if any(bundled(path) for path in os.environ.get("PATH", "").split(os.pathsep)):
        edits["PATH"] = os.pathsep.join(
            path for path in os.environ["PATH"].split(os.pathsep) if not bundled(path)
        )
    original = {key: os.environ.get(key) for key in edits}
    try:
        for key, value in edits.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def robot_web_url(host, shortcut):
    host = host.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        try:
            encoded = host.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise ValueError("请输入有效的机器人 IP 或主机名") from error
        labels = encoded.rstrip(".").split(".")
        if (
            not host
            or len(encoded) > 253
            or re.fullmatch(r"[0-9.]+", encoded)
            or not all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels
            )
        ):
            raise ValueError("机器人地址只填写 IP 或主机名，不要填写协议、端口或路径")
        host = encoded
    port, path = WEB_SHORTCUTS[shortcut]
    url = QUrl()
    url.setScheme("http")
    url.setHost(host)
    url.setPort(port)
    url.setPath(path)
    if not url.isValid() or not url.host():
        raise ValueError("机器人地址无效")
    return url
