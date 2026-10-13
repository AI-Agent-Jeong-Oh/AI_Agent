# macOS 앱 다운로드 페이지 배포

정적 페이지는 `site/`에 있고, GitHub Pages 워크플로는 `.github/workflows/deploy-download-page.yml`에 있습니다. 앱 소스와 PyInstaller 빌드 설정은 이 페이지에 포함하지 않습니다. 웹페이지는 브라우저에서 GitHub의 최신 정식 Release를 조회하며, 정확한 ZIP 자산이 등록되어 있을 때만 다운로드 버튼을 활성화합니다. 임의의 다운로드 URL을 미리 연결하지 않습니다.

## 1. 앱 ZIP 준비

macOS에서 현재 `dist/VideoAnalysisAgent.app`을 확인한 뒤 저장소 루트에서 실행합니다.

```bash
./scripts/package_macos_release.sh
```

결과는 `dist/숭재수사대-다연캅-macOS-arm64.zip`입니다. 압축을 풀면 `숭재수사대 다연캅.app`이 나오며, 앱 내부 아이콘과 실행 파일은 원래 번들의 것을 사용합니다. ZIP은 `.gitignore` 대상이며 Git에 커밋하지 않습니다. 빌드를 갱신했다면 새 `.app`을 만든 뒤 ZIP도 다시 만드세요.

현재 번들은 Apple Silicon용 개발 빌드이며 개발자 인증서 서명과 공증, 다른 Mac에서의 실행은 검증되지 않았습니다. 로컬 분석에는 받는 Mac의 FFmpeg/ffprobe, Ollama, 영상 모델이 별도로 필요합니다.

## 2. GitHub Release 등록

이 저장소의 **Releases → Draft a new release**에서 버전 태그를 정하고, 위 ZIP을 자산으로 첨부하여 정식 Release를 게시합니다. 초안 또는 prerelease는 페이지의 `latest` 조회 대상이 아닙니다. GitHub가 한글 자산명을 변환하므로 등록한 자산의 이름을 `dayeoncop-macOS-arm64.zip`으로 변경해 `site/config.js`의 `assetName`과 정확히 맞추세요. ZIP 안의 앱 이름은 `숭재수사대 다연캅.app`으로 유지됩니다. 이후 자산 이름을 바꿀 때는 `site/config.js`도 수정합니다.

Release가 없는 동안, 또는 자산 이름이 다르면 버튼은 비활성 상태와 안내 문구를 표시합니다. 페이지에는 ZIP 파일을 올리지 않습니다.

## 3. GitHub Pages 설정

웹페이지 변경을 기본 브랜치 `main`에 반영한 뒤, 저장소 **Settings → Pages → Build and deployment → Source**에서 **GitHub Actions**를 선택합니다. `main`의 `site/` 또는 배포 워크플로가 바뀌면 워크플로가 정적 파일만 업로드합니다. 필요하면 **Actions → Deploy download page → Run workflow**로 다시 배포할 수 있습니다. Pages 주소는 배포 성공 후 해당 워크플로의 `github-pages` 환경 URL에서 확인하세요.

로컬 확인은 저장소 루트에서 `python3 -m http.server 8000 --directory site`를 실행하고 `http://localhost:8000/`을 열면 됩니다. 실제 GitHub Release 자산의 다운로드 검증은 Release 게시 후 수행해야 합니다.
