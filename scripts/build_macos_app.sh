#!/bin/zsh
set -euo pipefail

script_dir=${0:A:h}
project_dir=${script_dir:h}

if [[ "$(uname -s)" != "Darwin" ]]; then
  print -u2 "이 개발용 .app은 macOS에서만 빌드할 수 있습니다."
  exit 2
fi

python_bin="$project_dir/.venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  print -u2 ".venv가 없습니다. README의 개발 환경 설치 절차를 먼저 실행하세요."
  exit 2
fi

cd "$project_dir"
"$python_bin" -m PyInstaller --noconfirm --clean packaging/video_analysis_agent.spec
print "개발용 번들: $project_dir/dist/VideoAnalysisAgent.app"
print "개발자 인증서 서명·공증·외부 배포는 수행하지 않았습니다(PyInstaller의 ad-hoc 서명만 적용될 수 있음)."
