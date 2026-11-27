#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    printf '用法：%s -v 版本号\n示例：%s -v 0.3.1\n' "${0##*/}" "${0##*/}"
}

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

version=
while getopts ':v:h' option; do
    case "$option" in
        v) version=${OPTARG#v} ;;
        h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
shift "$((OPTIND - 1))"
if [[ $# -ne 0 || ! "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
    usage >&2
    exit 2
fi

for executable in git gh docker python3 sha256sum; do
    command -v "$executable" >/dev/null || fail "缺少命令 $executable"
done

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
repo_root=$PWD
repository=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
tag="v$version"
appimage="RobotWebInstaller-$version-x86_64.AppImage"
windows_exe="RobotWebInstaller-$version-windows-x64.exe"
workflow=.github/workflows/windows-release.yml
output_dir="$repo_root/dist/$tag"
tag_ref="refs/tags/$tag"
remote_tag=$(git ls-remote --refs origin "$tag_ref")
if [[ -n "$remote_tag" ]]; then
    git fetch origin "$tag_ref:$tag_ref"
fi

if git show-ref --verify --quiet "$tag_ref"; then
    source_ref=$tag_ref
else
    [[ -z "$(git status --porcelain)" ]] || fail "请先提交本次要构建的代码和工作流。"
    source_ref=HEAD
fi
git cat-file -e "$source_ref:$workflow" 2>/dev/null || fail "构建提交缺少已提交的 Windows 工作流。"
docker info >/dev/null

temporary_dir=$(mktemp -d)
source_dir="$temporary_dir/source"
cleanup() {
    if [[ -f "$source_dir/.git" ]]; then
        git worktree remove --force "$source_dir"
    fi
    rmdir "$temporary_dir"
}
trap cleanup EXIT
git worktree add --detach "$source_dir" "$source_ref"

if [[ "$source_ref" = HEAD ]]; then
    python3 - "$source_dir" "$version" <<'PY'
from pathlib import Path
import re
import sys

root, version = Path(sys.argv[1]), sys.argv[2]
for filename, key in (("pyproject.toml", "version"), ("src/robot_installer/__init__.py", "__version__")):
    path = root / filename
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(rf'(?m)^{key} = "[^"]+"$', f'{key} = "{version}"', text)
    if count != 1:
        raise SystemExit(f"无法更新 {filename} 的版本号")
    if updated != text:
        path.write_text(updated, encoding="utf-8")
PY
    if ! git -C "$source_dir" diff --quiet; then
        git -C "$source_dir" commit -m "Build release $tag" -- pyproject.toml src/robot_installer/__init__.py
    fi
    git -C "$source_dir" tag "$tag"
fi

commit=$(git -C "$source_dir" rev-parse HEAD)
source_version=$(PYTHONPATH="$source_dir/src" python3 -c 'from robot_installer import __version__; print(__version__)')
[[ "$source_version" = "$version" ]] || fail "标签 $tag 中的应用版本是 $source_version。"
if [[ -z "$remote_tag" ]]; then
    # Push the tag separately so GitHub receives the tag build event.
    if ! git push origin "$tag_ref"; then
        git push "git@github.com:$repository.git" "$tag_ref"
    fi
fi

release_assets() {
    gh release view "$tag" --repo "$repository" --json assets \
        --jq '.assets[] | select(.state == "uploaded") | .name'
}

assets=$(release_assets 2>/dev/null || true)
if ! grep -Fxq -- "$appimage" <<< "$assets" || ! grep -Fxq -- "$appimage.sha256" <<< "$assets"; then
    mkdir -p -- "$output_dir"
    if grep -Fxq -- "$appimage" <<< "$assets"; then
        gh release download "$tag" --repo "$repository" --pattern "$appimage" --dir "$source_dir/dist"
        cp -- "$source_dir/dist/$appimage" "$output_dir/"
        (cd "$output_dir" && sha256sum "$appimage" > "$appimage.sha256")
    elif ! (cd "$output_dir" && sha256sum -c "$appimage.sha256" 2>/dev/null); then
        bash "$source_dir/scripts/build_in_docker.sh"
        bash "$source_dir/scripts/check_runtime.sh" "$appimage"
        cp -- "$source_dir/dist/$appimage" "$source_dir/dist/$appimage.sha256" "$output_dir/"
        (cd "$output_dir" && sha256sum -c "$appimage.sha256")
    fi
fi

if ! grep -Fxq -- "$windows_exe" <<< "$assets" || ! grep -Fxq -- "$windows_exe.sha256" <<< "$assets"; then
    printf '等待 GitHub Actions 构建 %s。\n' "$tag"
    run_id=
    for ((attempt = 0; attempt < 24; attempt++)); do
        run_id=$(gh run list --repo "$repository" --branch "$tag" --commit "$commit" --event push \
            --limit 1 --json databaseId --jq '.[0].databaseId // empty')
        [[ -z "$run_id" ]] || break
        sleep 5
    done
    [[ -n "$run_id" ]] || fail "GitHub Actions 尚未找到 $tag 的构建。"
    gh run watch "$run_id" --repo "$repository" --exit-status --interval 10
    assets=$(release_assets)
    grep -Fxq -- "$windows_exe" <<< "$assets" || fail "Releases 缺少 Windows EXE。"
    grep -Fxq -- "$windows_exe.sha256" <<< "$assets" || fail "Releases 缺少 Windows 校验文件。"
fi

if grep -Fxq -- "$appimage.sha256" <<< "$assets" && ! grep -Fxq -- "$appimage" <<< "$assets"; then
    gh release download "$tag" --repo "$repository" --pattern "$appimage.sha256" --dir "$source_dir/release-check"
    read -r existing_hash _ < "$source_dir/release-check/$appimage.sha256"
    read -r local_hash _ < "$output_dir/$appimage.sha256"
    [[ "$existing_hash" = "$local_hash" ]] || fail "Releases 已有的校验文件与本地 AppImage 不匹配。"
fi

for name in "$appimage" "$appimage.sha256"; do
    if ! grep -Fxq -- "$name" <<< "$assets"; then
        gh release upload "$tag" "$output_dir/$name" --repo "$repository"
    fi
done

gh release view "$tag" --repo "$repository" --json url --jq .url
