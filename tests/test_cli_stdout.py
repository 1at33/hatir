"""CLI girişi TextIOWrapper olmayan stdout'ta çökmez."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _py(kod):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [SRC, os.environ.get("PYTHONPATH")])))
    return subprocess.run([sys.executable, "-c", textwrap.dedent(kod)],
                          check=False, capture_output=True, text=True, encoding="utf-8", env=env)


def test_ayir_stringio_stdout_cikis_2():
    r = _py("""
        import io, runpy, sys
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        runpy.run_module("hatir.puanla.ayir", run_name="__main__")
    """)
    assert r.returncode == 2, r.stderr
    assert "AttributeError" not in r.stderr
