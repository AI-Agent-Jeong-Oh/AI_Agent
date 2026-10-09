# Repository working rules

These instructions apply to the whole repository.

- Read `README.md`, `docs/architecture.md`, `docs/contracts.md`, and the current Git status before changing code.
- Preserve user changes. Work on a feature branch; do not force-push, push, open/merge a PR, or change `main` unless explicitly requested.
- Keep the app local-first: select video paths directly and do not introduce a web upload/server architecture without an approved design change.
- Never commit videos, dataset archives, labels, model weights, credentials, `.env`, personal outputs, logs, or caches.
- Do not pass ground-truth label JSON or classification directory names to an analyzer.
- Keep sample output visibly distinct from actual model output. Never claim that a model, dataset, GUI path, or platform was tested unless it was actually tested.
- `src/video_agent/contracts.py` and `python -m video_agent` are shared integration boundaries. Update the related docs and tests and request teammate review when they change.
- Local and external analyzers implement `Analyzer`; local mode must never fall back to an external API automatically.
- Report observations, evidence, and uncertainty separately. Do not infer criminal liability or a person's intent from video alone.
- Use bounded background work, honor cancellation, and use `progress=None` when real progress is unknown.
- Run `QT_QPA_PLATFORM=offscreen PYTHONPATH=src python3.13 -m unittest discover -s tests -v` for contract, analyzer, or UI workflow changes. Mark visible GUI, audio, real CCTV, real-model, signing/notarization, and distribution checks as unverified unless performed.
- FFprobe/FFmpeg operations must use original timeline seconds and bounded subprocesses; do not infer timestamps from frame index divided by FPS.
- Settings/results belong in the platform user-data directory and generated frames in the app cache directory. Cache cleanup requires app ownership markers.
