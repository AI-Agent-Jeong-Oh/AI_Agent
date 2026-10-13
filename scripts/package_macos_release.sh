#!/bin/zsh
set -euo pipefail

script_dir=${0:A:h}
project_dir=${script_dir:h}
source_app="${1:-$project_dir/dist/VideoAnalysisAgent.app}"
output_zip="${2:-$project_dir/dist/숭재수사대-다연캅-macOS-arm64.zip}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  print -u2 "macOS .app 패키징은 macOS에서 실행해 주세요."
  exit 2
fi

if [[ ! -f "$source_app/Contents/Info.plist" ]]; then
  print -u2 "앱 번들을 찾을 수 없습니다: $source_app"
  exit 2
fi

mkdir -p "${output_zip:h}"
staging_dir="$(mktemp -d "${TMPDIR:-/tmp}/dayeoncop-release.XXXXXX")"
trap 'rm -rf "$staging_dir"' EXIT

staged_app="$staging_dir/숭재수사대 다연캅.app"
ditto "$source_app" "$staged_app"
ditto -c -k --keepParent "$staged_app" "$staging_dir/release.zip"
mv "$staging_dir/release.zip" "$output_zip"

print "Release ZIP: $output_zip"
print "앱 번들 내부 이름과 빌드 설정은 변경하지 않았습니다."
