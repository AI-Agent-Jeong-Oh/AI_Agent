# Development-only unsigned macOS bundle. Build from the repository root.
from pathlib import Path

project_root = Path.cwd()
if not (project_root / "pyproject.toml").is_file():
    raise SystemExit("저장소 루트에서 packaging/video_analysis_agent.spec을 빌드해야 합니다.")

analysis = Analysis(
    [str(project_root / "src" / "video_agent" / "__main__.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=[
        (
            str(project_root / "src" / "video_agent" / "knowledge" / "data" / "common_rules.json"),
            "video_agent/knowledge/data",
        ),
        (
            str(project_root / "src" / "video_agent" / "knowledge" / "data" / "case_rules.json"),
            "video_agent/knowledge/data",
        ),
        (str(project_root / "src" / "video_agent" / "ui" / "assets"), "video_agent/ui/assets"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="VideoAnalysisAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="VideoAnalysisAgent",
)
app = BUNDLE(
    collection,
    name="VideoAnalysisAgent.app",
    icon=None,
    bundle_identifier="org.ai-agent-jeong-oh.video-analysis-agent.dev",
    version="0.1.0",
    info_plist={
        "CFBundleDisplayName": "영상 중요 구간 분석 Agent",
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
    },
)
