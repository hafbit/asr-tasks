from __future__ import annotations

from dataclasses import dataclass

from pypinyin import lazy_pinyin
from rapidfuzz.fuzz import ratio


@dataclass(frozen=True)
class ReplacementMatch:
    source: str
    target: str
    method: str
    score: float


def _pinyin(value: str) -> str:
    return "".join(lazy_pinyin(value)).lower().replace(" ", "")


def apply_terminology(
    text: str,
    *,
    hotwords: list[str],
    replacements: dict[str, str],
    fuzzy_threshold: int = 90,
) -> tuple[str, list[dict]]:
    updated = text
    matches: list[ReplacementMatch] = []

    for source, target in replacements.items():
        if source and source in updated and source != target:
            count = updated.count(source)
            updated = updated.replace(source, target)
            matches.extend(
                ReplacementMatch(source, target, "explicit", 100.0) for _ in range(count)
            )

    for target in hotwords:
        if not target or target in updated or len(target) < 2:
            continue
        target_pinyin = _pinyin(target)
        best: tuple[float, int, int, str] | None = None
        minimum = max(1, len(target) - 1)
        maximum = min(len(updated), len(target) + 1)
        for length in range(minimum, maximum + 1):
            for start in range(0, len(updated) - length + 1):
                candidate = updated[start : start + length]
                if candidate.isspace() or candidate in hotwords:
                    continue
                score = ratio(_pinyin(candidate), target_pinyin)
                if score >= fuzzy_threshold and (best is None or score > best[0]):
                    best = (score, start, start + length, candidate)
        if best is not None:
            score, start, end, source = best
            updated = f"{updated[:start]}{target}{updated[end:]}"
            matches.append(ReplacementMatch(source, target, "pinyin_fuzzy", score))

    return updated, [match.__dict__ for match in matches]
