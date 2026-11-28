"""Recognize the pdfminer implementations that the Rust cm path replaces."""

import sys
from collections.abc import Callable, Mapping
from hashlib import sha256
from types import CodeType, FunctionType

from pdfminer import casting, pdfinterp, utils


# CPython fingerprints from the pdfminer.six 20260107 release, in the
# order do_cm, safe_matrix, safe_float, mult_matrix. Source paths and line
# tables are intentionally excluded so installed .pyc files and wheels agree.
_CODE_DIGESTS = {
    (3, 11): "67890eaa444b67bd446308edbad08f9326382e59196376c77af690d14f968d53",
    (3, 12): "a97f4ef250e67df5d805e5ae58c011c6d59f4667fdc570ffa5bb34244dd04257",
}


def _code_signature(code: CodeType) -> tuple[object, ...]:
    return (
        code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount,
        code.co_nlocals, code.co_stacksize, code.co_flags, code.co_code,
        code.co_consts, code.co_names, code.co_varnames, code.co_freevars,
        code.co_cellvars, code.co_exceptiontable,
    )


def _float_binding(function: FunctionType) -> object:
    globals_: dict[str, object] = function.__globals__
    builtins: dict[str, object] = getattr(function, "__builtins__", {})
    return globals_.get("float", builtins.get("float"))


def audit_cm() -> Callable[[], FunctionType | None] | None:
    """Build a stream preflight for the audited handler."""
    expected = _CODE_DIGESTS.get(sys.version_info[:2])
    if sys.implementation.name != "cpython" or expected is None:
        return None

    interpreter: Mapping[str, object] = vars(pdfinterp.PDFPageInterpreter)
    operators: Mapping[str, object] = vars(pdfinterp)
    casting_module: Mapping[str, object] = vars(casting)
    functions: list[FunctionType] = []
    for candidate, namespace in (
        (interpreter.get("do_cm"), operators),
        (operators.get("safe_matrix"), casting_module),
        (casting_module.get("safe_float"), casting_module),
        (operators.get("mult_matrix"), vars(utils)),
    ):
        if type(candidate) is not FunctionType or candidate.__globals__ is not namespace:
            return None
        if any(value is not None and type(value) is not str for value in candidate.__code__.co_consts):
            return None
        functions.append(candidate)

    codes = tuple(function.__code__ for function in functions)
    signature = tuple(_code_signature(code) for code in codes)
    if sha256(repr(signature).encode("utf-8")).hexdigest() != expected:
        return None

    handler, safe_matrix, safe_float, mult_matrix = functions
    handler_code, matrix_code, float_code, mult_code = codes

    def verified_handler() -> FunctionType | None:
        # Functions can be replaced, or have their code replaced in place.
        # Recheck the live bindings before preparing each specialized stream.
        if (interpreter.get("do_cm") is handler and handler.__code__ is handler_code
                and operators.get("safe_matrix") is safe_matrix and safe_matrix.__code__ is matrix_code
                and casting_module.get("safe_float") is safe_float and safe_float.__code__ is float_code
                and operators.get("mult_matrix") is mult_matrix and mult_matrix.__code__ is mult_code
                and _float_binding(safe_float) is type(0.0)):
            return handler
        return None

    return verified_handler
