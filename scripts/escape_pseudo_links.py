#!/usr/bin/env python3
"""Escape prose notation that CommonMark would otherwise parse as a link."""

from __future__ import annotations

import json
import re
import sys
from typing import Any


FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# A coefficient selector and polynomial factor, e.g. [X^(m-1)](1-A).
# A following power distinguishes the standalone prose form from a link;
# the alphabet excludes URL/path syntax such as dots, slashes and colons.
COEFFICIENT_FACTOR_RE = re.compile(
    r"\[[A-Za-z]\^[A-Za-z0-9+*^() -]+\]\([A-Za-z0-9+*^() -]+\)"
)


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def _inline_link_end(text: str, start: int) -> int | None:
    bracket_depth = 1
    cursor = start + 1
    while cursor < len(text):
        character = text[cursor]
        if character in "\r\n":
            return None
        if not _is_escaped(text, cursor):
            if character == "[":
                bracket_depth += 1
            elif character == "]":
                bracket_depth -= 1
                if bracket_depth == 0:
                    break
        cursor += 1
    if cursor == start + 1 or bracket_depth or text[cursor + 1 : cursor + 2] != "(":
        return None

    parenthesis_depth = 1
    cursor += 2
    while cursor < len(text):
        character = text[cursor]
        if character in "\r\n":
            return None
        if not _is_escaped(text, cursor):
            if character == "(":
                parenthesis_depth += 1
            elif character == ")":
                parenthesis_depth -= 1
                if parenthesis_depth == 0:
                    return cursor + 1
        cursor += 1
    return None


def escape_pseudo_links(content: str) -> str:
    output: list[str] = []
    fence: tuple[str, int] | None = None
    code_span: int | None = None
    math_close: str | None = None

    for line in content.splitlines(keepends=True):
        fence_match = FENCE_OPEN_RE.match(line)
        if fence is not None:
            output.append(line)
            if fence_match is not None:
                marker = fence_match.group(1)
                remainder = line[fence_match.end() :].strip()
                if marker[0] == fence[0] and len(marker) >= fence[1] and not remainder:
                    fence = None
            continue
        if code_span is None and math_close is None and fence_match is not None:
            marker = fence_match.group(1)
            fence = (marker[0], len(marker))
            output.append(line)
            continue

        cursor = 0
        while cursor < len(line):
            if code_span is not None:
                if line[cursor] == "`":
                    end = cursor
                    while end < len(line) and line[end] == "`":
                        end += 1
                    output.append(line[cursor:end])
                    if end - cursor == code_span:
                        code_span = None
                    cursor = end
                else:
                    output.append(line[cursor])
                    cursor += 1
                continue

            if math_close is not None:
                if line.startswith(math_close, cursor) and not _is_escaped(line, cursor):
                    output.append(math_close)
                    cursor += len(math_close)
                    math_close = None
                else:
                    output.append(line[cursor])
                    cursor += 1
                continue

            if line[cursor] == "`":
                end = cursor
                while end < len(line) and line[end] == "`":
                    end += 1
                output.append(line[cursor:end])
                code_span = end - cursor
                cursor = end
                continue

            delimiter: tuple[str, str] | None = None
            if line.startswith("\\(", cursor) and not _is_escaped(line, cursor):
                delimiter = ("\\(", "\\)")
            elif line.startswith("\\[", cursor) and not _is_escaped(line, cursor):
                delimiter = ("\\[", "\\]")
            elif line[cursor] == "$" and not _is_escaped(line, cursor):
                delimiter = ("$$", "$$") if line.startswith("$$", cursor) else ("$", "$")
            if delimiter is not None:
                output.append(delimiter[0])
                cursor += len(delimiter[0])
                math_close = delimiter[1]
                continue

            if (
                line[cursor] == "["
                and not _is_escaped(line, cursor)
                and (link_end := _inline_link_end(line, cursor)) is not None
                and (
                    (cursor > 0 and not line[cursor - 1].isspace()
                     and line[cursor - 1] != "]")
                    or (
                        COEFFICIENT_FACTOR_RE.fullmatch(line[cursor:link_end]) is not None
                        and line[link_end : link_end + 1] == "^"
                    )
                )
            ):
                output.append("\\[")
                cursor += 1
                continue

            output.append(line[cursor])
            cursor += 1

    return "".join(output)


def preprocess_book(book: dict[str, Any]) -> dict[str, Any]:
    def visit(items: object) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            chapter = item.get("Chapter")
            if not isinstance(chapter, dict):
                continue
            content = chapter.get("content")
            if isinstance(content, str):
                chapter["content"] = escape_pseudo_links(content)
            visit(chapter.get("sub_items"))

    visit(book.get("items"))
    return book


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments[:1] == ["supports"]:
        return 0 if len(arguments) == 2 and arguments[1] == "html" else 1
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, list) or len(payload) != 2 or not isinstance(payload[1], dict):
            raise ValueError("expected mdBook [context, book] input")
        json.dump(preprocess_book(payload[1]), sys.stdout, ensure_ascii=False)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"escape-pseudo-links: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
