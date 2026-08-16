from __future__ import annotations

import pytest

from asr_tasks.config import Settings
from asr_tasks.security import validate_source_url


def test_blocks_loopback_url(settings: Settings) -> None:
    with pytest.raises(ValueError, match="blocked"):
        validate_source_url("http://127.0.0.1/video.mp4", settings)


def test_allows_explicit_private_host(settings: Settings) -> None:
    settings.source_url_allowed_hosts = ["storage.internal"]
    assert (
        validate_source_url("https://storage.internal/video.mp4", settings)
        == "https://storage.internal/video.mp4"
    )


def test_rejects_credentials(settings: Settings) -> None:
    with pytest.raises(ValueError, match="credentials"):
        validate_source_url("https://user:password@example.com/a", settings)
