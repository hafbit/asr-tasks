from __future__ import annotations

import argparse
import json
from pathlib import Path

MODELS = {
    "paraformer": "iic/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404",
    "vad": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    "punctuation": "iic/punc_ct-transformer_cn-en-common-vocab471067-large",
}


def download_models(
    destination: Path,
    *,
    paraformer_revision: str,
    vad_revision: str,
    punctuation_revision: str,
) -> None:
    try:
        from modelscope import snapshot_download
    except ImportError as error:
        raise RuntimeError("ModelScope is required to download the full image models") from error

    destination.mkdir(parents=True, exist_ok=True)
    revisions = {
        "paraformer": paraformer_revision,
        "vad": vad_revision,
        "punctuation": punctuation_revision,
    }
    manifest: dict[str, dict[str, str]] = {}
    for name, model_id in MODELS.items():
        target = destination / name
        snapshot_download(model_id, revision=revisions[name], local_dir=str(target))
        if not target.is_dir() or not any(target.iterdir()):
            raise RuntimeError(f"downloaded model directory is empty: {target}")
        manifest[name] = {
            "model_id": model_id,
            "revision": revisions[name],
            "path": str(target),
        }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Download pinned models for the full image")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--paraformer-revision", default="v2.0.5")
    parser.add_argument("--vad-revision", default="v2.0.4")
    parser.add_argument("--punctuation-revision", default="v2.0.4")
    args = parser.parse_args()
    download_models(
        args.destination,
        paraformer_revision=args.paraformer_revision,
        vad_revision=args.vad_revision,
        punctuation_revision=args.punctuation_revision,
    )


if __name__ == "__main__":
    main()
