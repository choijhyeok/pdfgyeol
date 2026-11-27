from collections.abc import Callable
from typing import Any

def parse_content(
    data: bytes,
    literal: Callable[[str | bytes], Any],
    keyword: Callable[[bytes], Any],
) -> list[tuple[list[Any], str | None]] | None: ...
