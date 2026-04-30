from __future__ import annotations


UNSUPPORTED_BRACED_LATEX_COMMANDS = ("boxed", "fbox", "framebox")


def sanitize_latex_content(text: str) -> str:
    """Remove unsupported LaTeX wrapper commands while preserving their contents."""
    clean_text = text
    while True:
        next_text = clean_text
        for command in UNSUPPORTED_BRACED_LATEX_COMMANDS:
            next_text = remove_latex_command_with_braces(next_text, command)
        next_text = remove_latex_command_with_braces(next_text, "colorbox", preserve_last_argument=True)
        if next_text == clean_text:
            return clean_text
        clean_text = next_text


def remove_latex_command_with_braces(
    text: str,
    command: str,
    *,
    preserve_last_argument: bool = False,
) -> str:
    target = "\\" + command
    index = 0
    output: list[str] = []

    while index < len(text):
        if text.startswith(target, index):
            after_command = index + len(target)
            if _is_latex_command_boundary(text, after_command):
                brace_start = _skip_optional_arguments(text, _skip_whitespace(text, after_command))
                if brace_start < len(text) and text[brace_start] == "{":
                    arguments = _read_braced_arguments(text, brace_start)
                    if arguments:
                        preserved = arguments[-1] if preserve_last_argument else arguments[0]
                        output.append(preserved[0])
                        index = preserved[1] + 1
                        continue

        output.append(text[index])
        index += 1

    return "".join(output)


def _is_latex_command_boundary(text: str, index: int) -> bool:
    return index >= len(text) or not text[index].isalpha()


def _skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _skip_optional_arguments(text: str, index: int) -> int:
    while index < len(text) and text[index] == "[":
        closing_index = _find_matching_square_bracket(text, index)
        if closing_index == -1:
            return index
        index = _skip_whitespace(text, closing_index + 1)
    return index


def _read_braced_arguments(text: str, index: int) -> list[tuple[str, int]]:
    arguments: list[tuple[str, int]] = []
    while index < len(text) and text[index] == "{":
        closing_index = _find_matching_brace(text, index)
        if closing_index == -1:
            break
        arguments.append((text[index + 1 : closing_index], closing_index))
        index = _skip_whitespace(text, closing_index + 1)
    return arguments


def _find_matching_brace(text: str, opening_index: int) -> int:
    depth = 0
    index = opening_index

    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1

    return -1


def _find_matching_square_bracket(text: str, opening_index: int) -> int:
    depth = 0
    index = opening_index

    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index
        index += 1

    return -1
