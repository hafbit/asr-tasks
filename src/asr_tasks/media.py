from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import numpy as np
import soundfile as sf

from .config import Settings
from .security import validate_source_url

ProgressCallback = Callable[[float], None]
CancelCallback = Callable[[], bool]


class MediaError(RuntimeError):
    pass


class CancelledError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    duration_seconds: float
    has_audio: bool


@dataclass(frozen=True)
class AudioWindow:
    index: int
    samples: np.ndarray
    sample_rate: int
    read_start_ms: int
    canonical_start_ms: int
    canonical_end_ms: int
    is_last: bool


def probe_media(path: Path) -> MediaInfo:
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise MediaError(f"ffprobe failed: {process.stderr.strip()}")
    try:
        payload = json.loads(process.stdout)
        duration = float(payload.get("format", {}).get("duration") or 0.0)
        has_audio = any(item.get("codec_type") == "audio" for item in payload.get("streams", []))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise MediaError("ffprobe returned invalid metadata") from error
    if not has_audio:
        raise MediaError("media does not contain an audio stream")
    if duration <= 0:
        raise MediaError("media duration is unavailable or zero")
    return MediaInfo(duration_seconds=duration, has_audio=has_audio)


def _download_filename(url: str, content_type: str | None) -> str:
    name = Path(urlparse(url).path).name
    if name and "." in name:
        suffix = Path(name).suffix[:16]
    else:
        suffix = mimetypes.guess_extension((content_type or "").split(";")[0]) or ".bin"
    return f"download{suffix}"


def download_source(
    url: str,
    destination_dir: Path,
    settings: Settings,
    *,
    progress: ProgressCallback,
    cancelled: CancelCallback,
) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    current_url = validate_source_url(url, settings)
    with httpx.Client(timeout=settings.download_timeout_seconds, follow_redirects=False) as client:
        response: httpx.Response | None = None
        for _ in range(settings.source_url_max_redirects + 1):
            response = client.send(client.build_request("GET", current_url), stream=True)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                response.close()
                if not location:
                    raise MediaError("download redirect is missing a location")
                current_url = validate_source_url(urljoin(current_url, location), settings)
                continue
            break
        else:
            raise MediaError("download exceeded redirect limit")
        assert response is not None
        try:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or 0)
            if total > settings.max_upload_bytes:
                raise MediaError("remote asset is too large")
            target = destination_dir / _download_filename(
                current_url, response.headers.get("content-type")
            )
            temporary = target.with_suffix(f"{target.suffix}.part")
            written = 0
            try:
                with temporary.open("wb") as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        if cancelled():
                            raise CancelledError("job cancelled while downloading")
                        written += len(chunk)
                        if written > settings.max_upload_bytes:
                            raise MediaError("remote asset is too large")
                        output.write(chunk)
                        progress(written / total if total else 0.0)
                temporary.replace(target)
                progress(1.0)
                return target
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        finally:
            response.close()


_OUT_TIME_RE = re.compile(r"^out_time_(?:ms|us)=(\d+)$")


def extract_audio(
    source: Path,
    destination: Path,
    *,
    duration_seconds: float,
    progress: ProgressCallback,
    cancelled: CancelCallback,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f"{destination.suffix}.part")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "flac",
        "-f",
        "flac",
        "-progress",
        "pipe:1",
        "-nostats",
        str(temporary),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if cancelled():
                process.terminate()
                raise CancelledError("job cancelled while extracting audio")
            match = _OUT_TIME_RE.match(line.strip())
            if match and duration_seconds > 0:
                # ffmpeg currently reports microseconds for both keys on supported builds.
                out_seconds = int(match.group(1)) / 1_000_000
                progress(min(1.0, out_seconds / duration_seconds))
        stderr = process.stderr.read() if process.stderr else ""
        return_code = process.wait()
        if return_code != 0:
            raise MediaError(f"ffmpeg failed: {stderr.strip()}")
        temporary.replace(destination)
        progress(1.0)
    except Exception:
        if process.poll() is None:
            process.kill()
            process.wait()
        temporary.unlink(missing_ok=True)
        raise


def iter_audio_windows(
    path: Path, *, window_seconds: int, overlap_seconds: int
) -> Iterator[AudioWindow]:
    with sf.SoundFile(path) as audio:
        sample_rate = int(audio.samplerate)
        total_frames = len(audio)
        total_ms = int(total_frames * 1000 / sample_rate)
        window_ms = window_seconds * 1000
        overlap_ms = overlap_seconds * 1000
        index = 0
        canonical_start_ms = 0
        while canonical_start_ms < total_ms:
            canonical_end_ms = min(total_ms, canonical_start_ms + window_ms)
            read_start_ms = max(0, canonical_start_ms - overlap_ms)
            read_end_ms = min(total_ms, canonical_end_ms + overlap_ms)
            audio.seek(int(read_start_ms * sample_rate / 1000))
            frames = int((read_end_ms - read_start_ms) * sample_rate / 1000)
            samples = audio.read(frames, dtype="float32", always_2d=False)
            if samples.ndim > 1:
                samples = np.mean(samples, axis=1, dtype=np.float32)
            yield AudioWindow(
                index=index,
                samples=np.asarray(samples, dtype=np.float32),
                sample_rate=sample_rate,
                read_start_ms=read_start_ms,
                canonical_start_ms=canonical_start_ms,
                canonical_end_ms=canonical_end_ms,
                is_last=canonical_end_ms >= total_ms,
            )
            canonical_start_ms = canonical_end_ms
            index += 1


def canonicalize_vad_segments(
    window: AudioWindow, relative_segments: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    accepted: list[tuple[int, int]] = []
    for relative_start, relative_end in relative_segments:
        start = max(0, window.read_start_ms + int(relative_start))
        end = window.read_start_ms + int(relative_end)
        global_midpoint = window.read_start_ms + (int(relative_start) + int(relative_end)) / 2
        upper_matches = (
            global_midpoint <= window.canonical_end_ms
            if window.is_last
            else global_midpoint < window.canonical_end_ms
        )
        if global_midpoint >= window.canonical_start_ms and upper_matches and end > start:
            accepted.append((start, end))
    return accepted


def read_audio_segments(path: Path, boundaries: list[tuple[int, int]]) -> list[np.ndarray]:
    results: list[np.ndarray] = []
    with sf.SoundFile(path) as audio:
        sample_rate = int(audio.samplerate)
        for start_ms, end_ms in boundaries:
            audio.seek(int(start_ms * sample_rate / 1000))
            frames = max(0, int((end_ms - start_ms) * sample_rate / 1000))
            samples = audio.read(frames, dtype="float32", always_2d=False)
            if samples.ndim > 1:
                samples = np.mean(samples, axis=1, dtype=np.float32)
            results.append(np.asarray(samples, dtype=np.float32))
    return results
