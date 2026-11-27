#!/bin/bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
docker build -f packaging/Dockerfile -t robot-web-installer:build .
container_id=$(docker create robot-web-installer:build)
trap 'docker rm "$container_id" >/dev/null' EXIT
mkdir -p dist
docker cp "$container_id:/app/dist/." dist/
echo 'Build artifacts copied to dist/'
