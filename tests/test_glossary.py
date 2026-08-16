from asr_tasks.glossary import apply_terminology


def test_explicit_and_fuzzy_terminology_replacement() -> None:
    text, matches = apply_terminology(
        "欢迎使用哈福比特和万维灵书",
        hotwords=["万维灵枢"],
        replacements={"哈福比特": "hafbit"},
        fuzzy_threshold=85,
    )

    assert text == "欢迎使用hafbit和万维灵枢"
    assert {item["method"] for item in matches} == {"explicit", "pinyin_fuzzy"}


def test_existing_hotword_is_not_replaced() -> None:
    text, matches = apply_terminology(
        "万维灵枢",
        hotwords=["万维灵枢"],
        replacements={},
    )
    assert text == "万维灵枢"
    assert matches == []
