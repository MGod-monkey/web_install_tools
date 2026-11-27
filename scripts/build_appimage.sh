#!/bin/bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
python_bin=${PYTHON_BIN:-python3}
"$python_bin" -m PyInstaller --noconfirm --clean --onedir --windowed \
    --name robot-web-installer --paths src --collect-all robot_installer \
    --exclude-module tkinter --exclude-module matplotlib --exclude-module numpy run.py
app_dir=build/RobotWebInstaller.AppDir
"$python_bin" -c 'import shutil; shutil.rmtree("build/RobotWebInstaller.AppDir", ignore_errors=True)'
mkdir -p "$app_dir/usr/bin"
cp -a dist/robot-web-installer/. "$app_dir/usr/bin/"
"$python_bin" scripts/collect_system_libraries.py "$app_dir/usr/bin/_internal"
install -m 755 packaging/AppRun "$app_dir/AppRun"
install -m 644 packaging/robot-web-installer.desktop "$app_dir/robot-web-installer.desktop"
install -m 644 packaging/robot-web-installer.svg "$app_dir/robot-web-installer.svg"
cp packaging/robot-web-installer.svg "$app_dir/.DirIcon"
"$python_bin" scripts/collect_licenses.py "$app_dir/usr/share/doc/robot-web-installer/licenses"
mkdir -p "$app_dir/usr/share/robot-web-installer"
install -m 644 packaging/fonts.conf "$app_dir/usr/share/robot-web-installer/fonts.conf"
mkdir -p build/tools
runtime=build/tools/runtime-x86_64
if [ ! -s "$runtime" ]; then
    curl --fail --location --retry 3 \
        https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64 \
        --output "$runtime.part"
    mv "$runtime.part" "$runtime"
fi
if [ -f packaging/runtime.sha256 ]; then
    (cd build/tools && sha256sum -c ../../packaging/runtime.sha256)
fi
mksquashfs "$app_dir" build/robot-installer.squashfs -root-owned -noappend -comp gzip -quiet
version=$(PYTHONPATH=src "$python_bin" -c 'from robot_installer import __version__; print(__version__)')
output=${APPIMAGE_NAME:-RobotWebInstaller-$version-x86_64.AppImage}
cat "$runtime" build/robot-installer.squashfs > "dist/$output"
chmod +x "dist/$output"
(cd dist && sha256sum "$output" > "$output.sha256")
echo "AppImage: dist/$output"
