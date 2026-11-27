"""Bundle ELF dependencies omitted by freezer heuristics, except host glibc.

GPU vendor drivers remain host-provided; GL/GLX dispatch libraries are portable.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

bundle = Path(sys.argv[1])
excluded = re.compile(r"^(ld-linux|lib(c|m|pthread|dl|rt|resolv|util|anl|nss_[a-z]+|BrokenLocale)\.so)")
queue = [path for path in bundle.rglob("*") if path.is_file() and ".so" in path.name]
existing = {path.name for path in bundle.rglob("*")}
env = {**os.environ, "LD_LIBRARY_PATH": str(bundle.resolve())}
copied = []
while queue:
    library = queue.pop()
    result = subprocess.run(["ldd", str(library)], capture_output=True, text=True, env=env)
    for name, filename in re.findall(r"^\s*(\S+)\s+=>\s+(/\S+)", result.stdout, flags=re.M):
        if name in existing or excluded.match(name):
            continue
        destination = bundle / name
        shutil.copyfile(filename, destination)
        existing.add(name)
        copied.append(name)
        queue.append(destination)
print("Additional runtime libraries:", ", ".join(sorted(copied)))
