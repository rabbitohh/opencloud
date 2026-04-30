from __future__ import annotations

import base64
import struct
import html
import io
import re
from dataclasses import dataclass

from PySide6.QtGui import QTextDocumentFragment


_PLACEHOLDER_PREFIX = "OPENCLAWLATEXPLACEHOLDER"
_PLACEHOLDER_SUFFIX = "END"
_LATEX_BASE_DPI = 72
_LATEX_IMAGE_SCALE = 10


@dataclass(frozen=True)
class RenderedLatex:
    html: str
    width: int
    height: int


class LatexMarkdownRenderer:
    """Render Markdown containing common LaTeX math delimiters to Qt-friendly HTML."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, bool, int, str, int], RenderedLatex | None] = {}

    def has_latex(self, markdown: str) -> bool:
        protected = _split_protected_markdown(markdown or "")
        for text, protected_segment in protected:
            if protected_segment:
                continue
            if _find_latex_range(text, 0) is not None:
                return True
        return False

    def to_html(self, markdown: str, *, font_size: int, text_color: str, render_latex: bool = True) -> str:
        if not render_latex:
            return QTextDocumentFragment.fromMarkdown(markdown or " ").toHtml()

        protected = _split_protected_markdown(markdown or " ")
        pieces: list[str] = []
        rendered: dict[str, tuple[str, bool]] = {}
        counter = 0

        for text, protected_segment in protected:
            if protected_segment:
                pieces.append(text)
                continue

            replaced, segment_rendered, counter = self._replace_latex(
                text,
                start_index=counter,
                font_size=font_size,
                text_color=text_color,
            )
            pieces.append(replaced)
            rendered.update(segment_rendered)

        fragment = QTextDocumentFragment.fromMarkdown("".join(pieces))
        output = fragment.toHtml()
        for placeholder, (html_value, display) in sorted(rendered.items(), key=lambda item: len(item[0]), reverse=True):
            if display:
                block_re = re.compile(
                    rf"<p(?P<attrs>[^>]*)>\s*(?P<start><!--StartFragment-->)?{re.escape(placeholder)}"
                    rf"(?P<end><!--EndFragment-->)?\s*</p>",
                    re.S,
                )
                output = block_re.sub(
                    lambda match: f'{match.group("start") or ""}{html_value}{match.group("end") or ""}',
                    output,
                )
            output = output.replace(placeholder, html_value)
        return output

    def _replace_latex(
        self,
        text: str,
        *,
        start_index: int,
        font_size: int,
        text_color: str,
    ) -> tuple[str, dict[str, tuple[str, bool]], int]:
        output: list[str] = []
        rendered: dict[str, tuple[str, bool]] = {}
        index = 0
        counter = start_index

        while index < len(text):
            latex_range = _find_latex_range(text, index)
            if latex_range is None:
                output.append(text[index:])
                break

            start, end, opening_delimiter, closing_delimiter, display = latex_range

            expression = text[start + len(opening_delimiter) : end].strip()
            if not expression:
                output.append(text[index : end + len(closing_delimiter)])
                index = end + len(closing_delimiter)
                continue

            output.append(text[index:start])
            placeholder = f"{_PLACEHOLDER_PREFIX}{counter}{_PLACEHOLDER_SUFFIX}"
            counter += 1
            output.append(placeholder)
            rendered[placeholder] = (
                self._latex_html(
                    expression,
                    opening_delimiter=opening_delimiter,
                    closing_delimiter=closing_delimiter,
                    display=display,
                    font_size=font_size,
                    text_color=text_color,
                ),
                display,
            )
            index = end + len(closing_delimiter)

        return "".join(output), rendered, counter

    def _latex_html(
        self,
        expression: str,
        *,
        opening_delimiter: str,
        closing_delimiter: str,
        display: bool,
        font_size: int,
        text_color: str,
    ) -> str:
        rendered = self._render_latex(expression, display=display, font_size=font_size, text_color=text_color)
        if rendered is None:
            fallback = f"{opening_delimiter}{expression}{closing_delimiter}"
            return html.escape(fallback)
        return rendered.html

    def _render_latex(self, expression: str, *, display: bool, font_size: int, text_color: str) -> RenderedLatex | None:
        key = (expression, display, font_size, text_color, _LATEX_IMAGE_SCALE)
        if key in self._cache:
            return self._cache[key]

        try:
            png, width, height = _math_to_png(
                expression,
                font_size=font_size + (3 if display else 0),
                text_color=text_color,
                scale=_LATEX_IMAGE_SCALE,
            )
        except Exception:
            self._cache[key] = None
            return None

        encoded = base64.b64encode(png).decode("ascii")
        src = f"data:image/png;base64,{encoded}"
        if display:
            image = f'<img src="{src}" width="{width}" height="{height}" />'
            result = RenderedLatex(
                f'<p align="center" style="margin: 8px 0 14px 0;">{image}</p>',
                width,
                height,
            )
        else:
            result = RenderedLatex(
                f'<img src="{src}" width="{width}" height="{height}" style="vertical-align: middle;" />',
                width,
                height,
            )
        self._cache[key] = result
        return result


def _math_to_png(expression: str, *, font_size: int, text_color: str, scale: int = 3) -> tuple[bytes, int, int]:
    import matplotlib as mpl
    from matplotlib.figure import Figure
    from matplotlib.mathtext import MathTextParser

    scale = max(1, scale)
    dpi = _LATEX_BASE_DPI * scale
    math_expression = f"${expression}$"
    with mpl.rc_context({"mathtext.fontset": "cm", "text.parse_math": True}):
        MathTextParser("agg").parse(math_expression, dpi=dpi)
        fig = Figure(figsize=(0.01, 0.01), dpi=dpi)
        fig.patch.set_alpha(0)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis("off")
        ax.text(0, 0, math_expression, color=text_color, fontsize=font_size, parse_math=True)
        buffer = io.BytesIO()
        fig.savefig(
            buffer,
            format="png",
            transparent=True,
            bbox_inches="tight",
            pad_inches=0.04,
            dpi=dpi,
        )
        data = buffer.getvalue()

    pixel_width, pixel_height = _png_size(data)
    return data, max(1, round(pixel_width / scale)), max(1, round(pixel_height / scale))


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return 80, 28
    return struct.unpack(">II", data[16:24])


def _split_protected_markdown(markdown: str) -> list[tuple[str, bool]]:
    segments: list[tuple[str, bool]] = []
    position = 0

    for match in re.finditer(r"(^|\n)(```|~~~)[^\n]*\n[\s\S]*?(?:\n\2(?=\n|$)|$)", markdown):
        if match.start() > position:
            segments.extend(_split_inline_code(markdown[position : match.start()]))
        segments.append((match.group(0), True))
        position = match.end()

    if position < len(markdown):
        segments.extend(_split_inline_code(markdown[position:]))
    return segments


def _split_inline_code(text: str) -> list[tuple[str, bool]]:
    segments: list[tuple[str, bool]] = []
    position = 0
    pattern = re.compile(r"(`+)([\s\S]*?)\1")
    for match in pattern.finditer(text):
        if match.start() > position:
            segments.append((text[position : match.start()], False))
        segments.append((match.group(0), True))
        position = match.end()
    if position < len(text):
        segments.append((text[position:], False))
    return segments


def _find_inline_dollar_latex_end(text: str, start: int) -> int:
    index = start
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == "$":
            if index + 1 < len(text) and text[index + 1] == "$":
                index += 2
                continue
            return index
        index += 1
    return -1


def _find_closing_delimiter(text: str, start: int, closing_delimiter: str) -> int:
    index = start
    while index < len(text):
        if text[index] == "\\":
            if text.startswith(closing_delimiter, index):
                return index
            index += 2
            continue
        if text.startswith(closing_delimiter, index):
            return index
        index += 1
    return -1


def _find_opening_delimiter(text: str, index: int, opening_delimiter: str) -> int:
    start = text.find(opening_delimiter, index)
    while start != -1:
        if opening_delimiter not in "([" or _is_plain_latex_opening(text, start, opening_delimiter):
            return start
        start = text.find(opening_delimiter, start + 1)
    return -1


def _is_plain_latex_opening(text: str, start: int, opening_delimiter: str) -> bool:
    if start > 0 and text[start - 1] == "\\":
        return False

    content_start = start + len(opening_delimiter)
    if content_start >= len(text):
        return False

    next_char = text[content_start]
    if opening_delimiter == "(":
        if next_char not in " \t\r\n\\":
            return False
    elif next_char not in " \t\r\n\\":
        return False

    if start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_"):
        return False

    return True


def _skip_latex_escape_or_spacing_group(text: str, index: int, opening: str, closing: str) -> int:
    slash_end = index
    while slash_end < len(text) and text[slash_end] == "\\":
        slash_end += 1

    if slash_end < len(text) and text[slash_end] == opening:
        group_end = text.find(closing, slash_end + 1)
        if group_end != -1:
            return group_end + 1
        return slash_end + 1

    return min(len(text), index + 2)


def _find_balanced_latex_end(text: str, start: int, opening: str, closing: str) -> int:
    index = start + 1
    depth = 1
    while index < len(text):
        if text[index] == "\\":
            index = _skip_latex_escape_or_spacing_group(text, index, opening, closing)
            continue
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _looks_like_plain_delimited_latex(expression: str) -> bool:
    if re.search(r"[\u3400-\u9fff]", expression):
        return False
    if re.search(r"[\\_^=+\-*/{}]", expression):
        return True
    if re.fullmatch(r"[A-Za-z0-9(),.\s]+", expression):
        letters = re.sub(r"[^A-Za-z]", "", expression)
        return len(letters) <= 3 or any(char.isdigit() for char in expression)
    return False


def _find_latex_range(text: str, index: int) -> tuple[int, int, str, str, bool] | None:
    delimiters = (
        ("$$", "$$", True),
        (r"\[", r"\]", True),
        (r"\(", r"\)", False),
        ("$", "$", False),
    )

    while index < len(text):
        candidates = [
            (start, opening_delimiter, closing_delimiter, display)
            for opening_delimiter, closing_delimiter, display in delimiters
            if (start := _find_opening_delimiter(text, index, opening_delimiter)) != -1
        ]
        if not candidates:
            return None

        start, opening_delimiter, closing_delimiter, display = min(candidates, key=lambda item: item[0])
        content_start = start + len(opening_delimiter)
        if opening_delimiter == "$":
            end = _find_inline_dollar_latex_end(text, content_start)
        elif opening_delimiter in "([":
            end = _find_balanced_latex_end(text, start, opening_delimiter, closing_delimiter)
        else:
            end = _find_closing_delimiter(text, content_start, closing_delimiter)

        if end == -1:
            return None
        expression = text[content_start:end].strip()
        if expression and (
            opening_delimiter not in "([" or _looks_like_plain_delimited_latex(expression)
        ):
            return start, end, opening_delimiter, closing_delimiter, display
        index = end + len(closing_delimiter)

    return None
