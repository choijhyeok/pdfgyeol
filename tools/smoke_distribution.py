"""Install wheel and sdist into fresh environments and compare offline API and CLI output."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from tarfile import open as open_tar
from tempfile import TemporaryDirectory

from reportlab.pdfgen.canvas import Canvas


def run_install(python, artifact, wheelhouse, root, example):
    root.mkdir()
    env = os.environ.copy()
    for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "DECISION_MODEL_DEPLOYMENT",
                "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(key, None)
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_NO_INDEX="1", PIP_FIND_LINKS=wheelhouse.as_uri(),
               PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONNOUSERSITE="1", PYTHONIOENCODING="utf-8")
    subprocess.run([python, "-m", "venv", "venv"], cwd=root, env=env, check=True)
    scripts = root / "venv" / ("Scripts" if os.name == "nt" else "bin")
    interpreter = scripts / ("python.exe" if os.name == "nt" else "python")
    cli = scripts / ("pdfgyeol.exe" if os.name == "nt" else "pdfgyeol")
    subprocess.run([interpreter, "-m", "pip", "install", artifact], cwd=root, env=env, check=True)
    subprocess.run([interpreter, "-m", "pip", "check"], cwd=root, env=env, check=True)

    canvas = Canvas(str(root / "input.pdf"), invariant=1)
    canvas.drawString(30, 500, "Café observations")
    canvas.save()
    (root / "sitecustomize.py").write_text(
        "import os, sys\n"
        "def reject_network(event, args):\n"
        "    if event.startswith('socket.'):\n"
        "        os._exit(97)\n"
        "sys.addaudithook(reject_network)\n",
        encoding="utf-8",
    )
    env["PYTHONPATH"] = str(root)
    probe = subprocess.run([interpreter, "-c", "import socket; socket.socket()"], cwd=root, env=env)
    assert probe.returncode == 97

    def output(*argv):
        return subprocess.run(argv, cwd=root, env=env, check=True, capture_output=True).stdout

    assert output(interpreter, "-c", example).decode("utf-8").strip() == "Café observations"
    serialized = output(interpreter, "-c", "\n".join((
        "import sys",
        "from pathlib import Path",
        "import pdfgyeol",
        "assert Path(pdfgyeol.__file__).is_relative_to(sys.prefix)",
        "assert Path(pdfgyeol.__file__).with_name('py.typed').is_file()",
        "sys.stdout.write(pdfgyeol.parse_pdf('input.pdf').to_json())",
    )))
    data = json.loads(serialized)
    assert data["schema_version"] == "pdfgyeol.parse/1"
    assert data["decision_enabled"] is False and data["pages"][0]["decisions"] == []
    env["PYTHONIOENCODING"] = "ascii"
    assert output(cli, "input.pdf") == output(interpreter, "-m", "pdfgyeol", "input.pdf") == serialized
    assert output(cli, "input.pdf", "--output", "result.json") == b""
    assert (root / "result.json").read_bytes() == serialized
    print(f"{artifact.name}: installed API, CLI, module entry point and documentation example passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("sdist", type=Path)
    parser.add_argument("wheelhouse", type=Path, help="local build and runtime dependency wheels")
    parser.add_argument("--python", default=sys.executable, help="interpreter used for fresh environments")
    args = parser.parse_args()
    with open_tar(args.sdist, "r:gz") as archive:
        (member,) = [name for name in archive.getnames() if name.endswith("/docs/api-cli.md")]
        stream = archive.extractfile(member)
        assert stream is not None
        example = stream.read().decode("utf-8").split("```python\n", 1)[1].split("\n```", 1)[0]
    with TemporaryDirectory(prefix="pdfgyeol-dist-") as temporary:
        for artifact in (args.wheel, args.sdist):
            run_install(args.python, artifact.resolve(), args.wheelhouse.resolve(),
                        Path(temporary) / artifact.suffix[1:], example)


if __name__ == "__main__":
    main()
