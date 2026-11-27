#!/bin/bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
artifact_root=$(pwd)/dist
artifact=${1:-RobotWebInstaller-0.3.0-x86_64.AppImage}
for image in ros:noetic-ros-base-focal ubuntu:22.04 ubuntu:24.04; do
    echo "Runtime check: $image"
    docker run --rm --network none -e QT_QPA_PLATFORM=offscreen \
        --mount "type=bind,src=$artifact_root,dst=/artifacts,readonly" "$image" \
        "/artifacts/$artifact" --appimage-extract-and-run --smoke-test
done
