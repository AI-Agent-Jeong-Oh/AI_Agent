#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "${script_dir}/.." && pwd)"
venv_dir="${repo_dir}/.venv"
python_bin="${VIDEO_AGENT_PYTHON:-python3.13}"
install_dev=false
check_only=false

for argument in "$@"; do
  case "${argument}" in
    --dev) install_dev=true ;;
    --check-only) check_only=true ;;
    *)
      echo "알 수 없는 옵션: ${argument}" >&2
      echo "사용법: ./scripts/bootstrap_macos_dev.sh [--dev] [--check-only]" >&2
      exit 2
      ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "이 스크립트는 macOS 개발 환경을 대상으로 합니다." >&2
  exit 1
fi

if ! command -v "${python_bin}" >/dev/null 2>&1; then
  echo "Python 3.13을 찾지 못했습니다: ${python_bin}" >&2
  echo "Python 3.13 설치 후 VIDEO_AGENT_PYTHON으로 실행 파일을 지정할 수 있습니다." >&2
  exit 1
fi

python_version="$("${python_bin}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
if [[ "${python_version}" != 3.13.* ]]; then
  echo "이 프로젝트는 Python 3.13.x가 필요합니다. 현재: ${python_version}" >&2
  exit 1
fi

echo "저장소: ${repo_dir}"
echo "Python: ${python_bin} (${python_version})"

for tool_name in ffmpeg ffprobe ollama; do
  if command -v "${tool_name}" >/dev/null 2>&1; then
    echo "확인: ${tool_name}"
  else
    echo "미확인: ${tool_name} (자동 설치하지 않음)"
  fi
done

if [[ "${check_only}" == true ]]; then
  echo "환경 확인만 완료했습니다. 파일이나 패키지를 변경하지 않았습니다."
  exit 0
fi

if [[ ! -x "${venv_dir}/bin/python" ]]; then
  "${python_bin}" -m venv "${venv_dir}"
fi

"${venv_dir}/bin/python" -m pip install --upgrade pip
"${venv_dir}/bin/python" -m pip install -r "${repo_dir}/requirements.txt"
if [[ "${install_dev}" == true ]]; then
  "${venv_dir}/bin/python" -m pip install -r "${repo_dir}/requirements-dev.txt"
fi

echo "가상환경 준비 완료: ${venv_dir}"
echo "실행: cd '${repo_dir}' && PYTHONPATH=src ./.venv/bin/python -m video_agent"
echo "테스트: cd '${repo_dir}' && QT_QPA_PLATFORM=offscreen PYTHONPATH=src ./.venv/bin/python -m unittest discover -s tests -v"
echo "Ollama 모델은 자동 다운로드하지 않습니다. 사용할 모델은 팀 합의 후 별도로 설치하세요."
