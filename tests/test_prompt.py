from __future__ import annotations

from memsave.prompt import parse_answer


def test_parse_answer_tolerates_markdown_and_deduplicates_tags():
    answer = parse_answer(
        """```json
        {"category": "meme", "description": "  Кот   смотрит ", "text": "Hello", "tags": ["#кот", "кот", "торт"]}
        ```""",
        "fake",
    )
    assert answer.category == "meme"
    assert answer.category_label == "Мем"
    assert answer.description == "Кот смотрит"
    assert answer.text == "Hello"
    assert answer.tags == ["кот", "торт"]
    assert answer.tag_rows()[0] == ("Мем", "category")


def test_unknown_category_becomes_other():
    answer = parse_answer('{"category":"spaceship","description":"x","tags":[]}', "fake")
    assert answer.category == "other"
    assert answer.category_label == "Другое"


def test_missing_json_is_rejected():
    try:
        parse_answer("not json at all", "fake")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
