from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from asr_tasks.download_models import MODELS, download_models


def test_download_models_uses_pinned_revisions_and_writes_manifest(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, str, str]] = []

    def snapshot_download(model_id: str, *, revision: str, local_dir: str) -> None:
        calls.append((model_id, revision, local_dir))
        target = tmp_path / local_dir.rsplit("/", 1)[-1]
        target.mkdir(parents=True, exist_ok=True)
        (target / "config.yaml").write_text("model: fake", encoding="utf-8")

    monkeypatch.setitem(
        sys.modules, "modelscope", SimpleNamespace(snapshot_download=snapshot_download)
    )
    download_models(
        tmp_path,
        paraformer_revision="v2.0.5",
        vad_revision="v2.0.4",
        punctuation_revision="v2.0.4",
    )

    assert [item[0] for item in calls] == list(MODELS.values())
    assert [item[1] for item in calls] == ["v2.0.5", "v2.0.4", "v2.0.4"]
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["paraformer"]["revision"] == "v2.0.5"
