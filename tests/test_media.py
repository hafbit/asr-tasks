from __future__ import annotations

import numpy as np
import soundfile as sf

from asr_tasks.media import canonicalize_vad_segments, iter_audio_windows


def test_window_overlap_uses_midpoint_for_single_ownership(tmp_path) -> None:
    path = tmp_path / "audio.wav"
    sf.write(path, np.zeros(130 * 16_000, dtype=np.float32), 16_000)
    windows = list(iter_audio_windows(path, window_seconds=60, overlap_seconds=5))

    assert len(windows) == 3
    first = canonicalize_vad_segments(windows[0], [(58_000, 64_000)])
    second = canonicalize_vad_segments(windows[1], [(3_000, 9_000)])
    assert first == []
    assert second == [(58_000, 64_000)]


def test_last_window_accepts_end_midpoint(tmp_path) -> None:
    path = tmp_path / "audio.wav"
    sf.write(path, np.zeros(1_600, dtype=np.float32), 16_000)
    window = next(iter_audio_windows(path, window_seconds=60, overlap_seconds=2))
    assert canonicalize_vad_segments(window, [(0, 100)]) == [(0, 100)]
