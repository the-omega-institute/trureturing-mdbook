"""The one definition of which dollar-delimited spans of a Markdown blob are formulas.

The renderer replaces exactly these spans and the verifier expects exactly this many
KaTeX nodes per page, so the two can never disagree about what counts as math.

Rules, unchanged from the verifier that used to hold them alone: fenced code blocks
(``` or ~~~, indented at most three spaces, closed by a marker of the same character
at least as long) and inline code spans are not math; a ``$`` preceded by an odd
number of backslashes is text; ``$$…$$`` may span lines; ``$…$`` must close on its
own line.
"""

from __future__ import annotations

import re

FENCE_RE = re.compile(rb"(`{3,}|~{3,})(.*)$")


def _visible_offsets(line: bytes, base: int) -> list[int]:
    """Offsets of the bytes of ``line`` that are outside inline code spans."""
    offsets: list[int] = []
    index = 0
    while index < len(line):
        if line[index] != ord("`"):
            offsets.append(base + index)
            index += 1
            continue
        end = index
        while end < len(line) and line[end] == ord("`"):
            end += 1
        close = line.find(line[index:end], end)
        if close < 0:
            return offsets
        index = close + (end - index)
    return offsets


def math_spans(content: bytes) -> list[tuple[int, int, bool]]:
    """Return ``(start, end, display)`` for every formula, as offsets into ``content``."""
    text = bytearray()
    offsets: list[int] = []
    in_fence: tuple[bytes, int] | None = None
    position = 0
    for line in content.splitlines(keepends=True):
        base = position
        position += len(line)
        body = line.rstrip(b"\r\n")
        stripped = body.lstrip(b" ")
        indent = len(body) - len(stripped)
        fence_match = FENCE_RE.match(stripped) if indent <= 3 else None
        if fence_match:
            marker = fence_match.group(1)
            if in_fence is None:
                in_fence = (marker[:1], len(marker))
            elif (marker[:1] == in_fence[0] and len(marker) >= in_fence[1]
                  and not fence_match.group(2).strip()):
                in_fence = None
            continue
        if in_fence is not None:
            continue
        for offset in _visible_offsets(body, base):
            text.append(content[offset])
            offsets.append(offset)
        text.append(ord("\n"))
        offsets.append(base + len(body))

    def escaped(index: int) -> bool:
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and text[cursor] == ord("\\"):
            backslashes += 1
            cursor -= 1
        return backslashes % 2 == 1

    spans: list[tuple[int, int, bool]] = []
    index = 0
    dollar = ord("$")
    while index < len(text):
        if text[index] != dollar or escaped(index):
            index += 1
            continue
        display = text[index:index + 2] == b"$$"
        width = 2 if display else 1
        close = index + width
        search_end = len(text)
        if not display:
            line_end = text.find(b"\n", close)
            if line_end >= 0:
                search_end = line_end
        while close < search_end:
            if text[close:close + width] == b"$" * width and not escaped(close):
                spans.append((offsets[index], offsets[close + width - 1] + 1, display))
                index = close + width
                break
            close += 1
        else:
            index += width
    return spans
