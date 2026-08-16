from asr_tasks.glossary import apply_terminology


def test_explicit_and_fuzzy_terminology_replacement() -> None:
    text, matches = apply_terminology(
        "今天讨论量子计蒜和深度学席",
        hotwords=["深度学习"],
        replacements={"量子计蒜": "量子计算"},
        fuzzy_threshold=85,
    )

    assert text == "今天讨论量子计算和深度学习"
    assert {item["method"] for item in matches} == {"explicit", "pinyin_fuzzy"}


def test_existing_hotword_is_not_replaced() -> None:
    text, matches = apply_terminology(
        "深度学习",
        hotwords=["深度学习"],
        replacements={},
    )
    assert text == "深度学习"
    assert matches == []
