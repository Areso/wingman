#!/usr/bin/env bash
set -euo pipefail

plugin_dir="$(cd "$(dirname "$0")" && pwd -P)"
image="${WINGMAN_PDF_TO_EPUB_IMAGE:-wingman-pdf-to-epub:local}"
platform=""
push=false

usage() {
  cat <<'EOF'
Usage: ./build_image.sh [--platform PLATFORM[,PLATFORM...]] [--tag IMAGE] [--push]

Examples:
  ./build_image.sh
  ./build_image.sh --platform linux/arm64
  ./build_image.sh --platform linux/amd64,linux/arm64 --tag registry.example/pdf-to-epub:latest --push
EOF
}

while (($#)); do
  case "$1" in
    --platform)
      platform="${2:?--platform requires a value}"
      shift 2
      ;;
    --tag)
      image="${2:?--tag requires a value}"
      shift 2
      ;;
    --push)
      push=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! docker buildx version >/dev/null 2>&1; then
  echo "Docker Buildx is required for portable image builds." >&2
  exit 1
fi

build_args=(buildx build --tag "$image")
if [[ -n "$platform" ]]; then
  build_args+=(--platform "$platform")
fi

if [[ "$push" == true ]]; then
  build_args+=(--push)
elif [[ "$platform" == *,* ]]; then
  echo "A multi-platform image cannot be loaded locally. Add --push and use a registry tag." >&2
  exit 2
else
  build_args+=(--load)
fi

build_args+=("$plugin_dir")
docker "${build_args[@]}"
echo "Built container image: $image"
