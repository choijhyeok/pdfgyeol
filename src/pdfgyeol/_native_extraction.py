"""Optional content-stream lexer for the audited pdfminer interpreter."""

import logging
from collections.abc import Sequence
from typing import Any, Callable

from pdfminer import settings
from pdfminer.pdfinterp import PDFPageInterpreter
from pdfminer.pdftypes import stream_value
from pdfminer.psparser import KWD, LIT

_parse_content: Callable[
    [bytes, Callable[[str | bytes], Any], Callable[[bytes], Any]],
    list[tuple[list[Any], str | None]] | None,
] | None
try:
    from ._native import parse_content
except ImportError:
    _parse_content = None
else:
    _parse_content = parse_content


def native_available() -> bool:
    return _parse_content is not None


_LOGGERS = (logging.getLogger("pdfminer.pdfinterp"), logging.getLogger("pdfminer.psparser"))
_OPERATORS = {
    name.removeprefix("do_").replace("_a", "*").replace("_w", '"').replace("_q", "'"):
        (method, method.__code__.co_argcount - 1)
    for name, method in vars(PDFPageInterpreter).items()
    if name.startswith("do_")
}


class NativeInterpreter(PDFPageInterpreter):
    """Feed batched operands to stock pdfminer operator handlers."""

    def execute(self, streams: Sequence[object]) -> None:
        if (_parse_content is None or len(streams) != 1 or settings.STRICT
                or any(logger.isEnabledFor(logging.DEBUG) for logger in _LOGGERS)):
            super().execute(streams)
            return

        stream = stream_value(streams[0])
        # Preserve upstream tracking and warnings for ineligible stream IDs.
        if stream.objid is None or stream.objid in self.parent_stream_ids:
            super().execute(streams)
            return

        ops = _parse_content(stream.get_data(), LIT, KWD)
        if ops is None:
            super().execute(streams)
            return

        self.stream_ids.clear()
        self.stream_ids.add(stream.objid)
        # Match PDFPageInterpreter.execute's tolerant operator dispatch semantics.
        # In particular, unknown operators leave the existing operand stack intact.
        for operands, name in ops:
            self.argstack.extend(operands)
            if name is None:
                continue
            operator = _OPERATORS.get(name)
            if operator is not None:
                method, arity = operator
                args = self.pop(arity)
                if len(args) == arity:
                    method(self, *args)
