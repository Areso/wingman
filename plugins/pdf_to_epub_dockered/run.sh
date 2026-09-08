#!/usr/bin/env bash
set -euo pipefail

image="${WINGMAN_PDF_TO_EPUB_IMAGE:-wingman-pdf-to-epub:local}"

if (($# < 1)); then
  echo "Usage: $0 INPUT.pdf [OUTPUT.epub] [converter options]" >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Run ./install_dependencies.sh after installing Docker." >&2
  exit 1
fi

input_arg="$1"
shift
input_dir="$(cd "$(dirname "$input_arg")" && pwd -P)"
input_name="$(basename "$input_arg")"
input_path="$input_dir/$input_name"

if [[ ! -f "$input_path" ]]; then
  echo "Input PDF not found: $input_path" >&2
  exit 1
fi

if (($# > 0)) && [[ "$1" != --* ]]; then
  output_arg="$1"
  shift
  mkdir -p "$(dirname "$output_arg")"
  output_dir="$(cd "$(dirname "$output_arg")" && pwd -P)"
  output_name="$(basename "$output_arg")"
else
  output_dir="$input_dir"
  output_name="${input_name%.*}.epub"
fi

docker_args=(
  run --rm --network none
  --user "$(id -u):$(id -g)"
  --env HOME=/tmp
)

if [[ "$input_dir" == "$output_dir" ]]; then
  docker_args+=(--mount "type=bind,source=$input_dir,target=/work")
  container_input="/work/$input_name"
  container_output="/work/$output_name"
else
  docker_args+=(
    --mount "type=bind,source=$input_dir,target=/input,readonly"
    --mount "type=bind,source=$output_dir,target=/output"
  )
  container_input="/input/$input_name"
  container_output="/output/$output_name"
fi

docker "${docker_args[@]}" "$image" "$container_input" "$container_output" "$@"
