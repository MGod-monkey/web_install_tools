"""Include wheel license texts alongside the bundled dynamic libraries."""

from importlib.metadata import distribution
import json
from pathlib import Path
import shutil
import sys

target = Path(sys.argv[1])
target.mkdir(parents=True, exist_ok=True)
manifest = []
for name in [
    "PySide6-Essentials",
    "shiboken6",
    "paramiko",
    "requests",
    "cryptography",
    "bcrypt",
    "PyNaCl",
    "cffi",
    "pycparser",
    "charset-normalizer",
    "urllib3",
    "certifi",
    "idna",
    "pyinstaller",
    "pyte",
    "wcwidth",
]:
    package = distribution(name)
    folder = target / name
    folder.mkdir(exist_ok=True)
    for file in package.files or []:
        if any(word in file.name.upper() for word in ("LICENSE", "LICENCE", "COPYING", "NOTICE")):
            source = Path(package.locate_file(file))
            if source.is_file():
                shutil.copyfile(source, folder / str(file).replace("/", "_").replace("\\", "_"))
    manifest.append({"name": name, "version": package.version})
(target / "versions.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
