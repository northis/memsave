"""The vision prompt, response schema and tolerant answer normalization."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from . import config
from .text import normalize_tag, tag_key

CATEGORIES: dict[str, str] = {
    "meme": "Мем",
    "reaction": "Реакция",
    "screenshot": "Скриншот",
    "photo": "Фото",
    "art": "Арт",
    "text": "Текст",
    "other": "Другое",
}

SYSTEM_PROMPT = """You catalogue images for a personal meme archive.
Answer with one JSON object and nothing else - no markdown, no commentary.
Keys:
  category: one of "meme", "reaction", "screenshot", "photo", "art", "text", "other"
  description: one or two short sentences in Russian describing what is shown and,
               for a meme, the joke or context.
  text: every legible word in the image exactly as written, preserving line breaks,
        or null when the image has no text.
  tags: 3-6 Russian lowercase tags, most specific first: characters, franchises,
        meme names, animals, topics, emotions. No duplicates and no generic words
        like "картинка", "изображение" or "мем" (the category already covers it).

Rules:
  - Read the image; never invent details that are not visible.
  - If the image is a recognizable meme, put its name or reference in tags.
  - description is used for search; keep it short and concrete.
  - text is for search and must never be translated or paraphrased.
  - If the image is a screenshot with a lot of text, copy the meaningful text up to
    6000 characters.
"""


class RawAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str = "other"
    description: str = ""
    text: str | None = None
    tags: list[str] | str = Field(default_factory=list)


@dataclass(frozen=True)
class AiAnswer:
    category: str
    category_label: str
    description: str
    text: str | None
    tags: list[str]
    model: str
    raw_json: str

    def tag_rows(self) -> list[tuple[str, str]]:
        """Rows for image_tags: category first, then free tags."""
        rows: list[tuple[str, str]] = [(self.category_label, "category")]
        seen = {tag_key(self.category_label), tag_key(self.category)}
        for tag in self.tags:
            key = tag_key(tag)
            if key and key not in seen:
                seen.add(key)
                rows.append((tag, "tag"))
        return rows


def _balanced_object(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    start = value.find("{")
    if start < 0:
        raise ValueError("no JSON object in the answer")
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(value)):
        char = value[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return value[start : index + 1]
    raise ValueError("unbalanced JSON object in the answer")


def _clean_tags(value: list[str] | str) -> list[str]:
    if isinstance(value, str):
        value = [part for part in value.replace("#", "").split(",")]
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        tag = normalize_tag(item, config.MAX_TAG_LEN)
        key = tag_key(tag)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(tag)
        if len(result) >= config.MAX_TAGS:
            break
    return result


def parse_answer(content: str, model: str) -> AiAnswer:
    raw = _balanced_object(content)
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("answer is not valid JSON") from exc
    answer = RawAnswer.model_validate(data)
    category = str(answer.category or "other").strip().lower()
    if category not in CATEGORIES:
        category = "other"
    description = " ".join((answer.description or "").split())[: config.MAX_DESCRIPTION]
    text = answer.text
    if text is not None:
        text = str(text)[: config.MAX_TEXT].strip() or None
    return AiAnswer(
        category=category,
        category_label=CATEGORIES[category],
        description=description,
        text=text,
        tags=_clean_tags(answer.tags),
        model=model,
        raw_json=content[:40000],
    )


def build_request(data: bytes, media_type: str, model: str) -> dict:
    encoded = base64.b64encode(data).decode("ascii")
    image_url = f"data:{media_type};base64,{encoded}"
    return {
        "model": model,
        "temperature": 0,
        "max_tokens": 2000,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Catalogue this image and return the JSON object.",
                    },
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
    }
