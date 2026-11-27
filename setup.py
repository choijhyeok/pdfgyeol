import os

from setuptools import setup


if os.environ.get("PDFGYEOL_BUILD_RUST") == "1":
    from setuptools_rust import RustExtension

    setup(
        rust_extensions=[
            RustExtension("pdfgyeol._native", debug=False, cargo_manifest_args=["--locked"]),
        ],
        options={"bdist_wheel": {"py_limited_api": "cp311"}},
    )
else:
    setup()
