from __future__ import annotations

import importlib
import os
import sys


def import_typ2svg_module(monkeypatch, tmp_path, name: str):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for command in ("typst", "mutool"):
        path = bindir / command
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    sys.modules.pop("typ2svg", None)
    return importlib.import_module(name)


def test_main_calls_typ2svg_with_cli_args(monkeypatch, tmp_path, capsys):
    cli = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.__main__")
    calls = []
    output = tmp_path / "output.svg"

    def fake_typ2svg(*args, **kwargs):
        calls.append((args, kwargs))
        return output

    monkeypatch.setattr(cli, "typ2svg", fake_typ2svg)

    assert cli.main([
        "input.typ",
        str(output),
        "--root",
        ".",
        "--font-path",
        "fonts",
        "--strict-fonts",
    ]) == 0

    assert calls == [((
        cli.Path("input.typ"),
        output,
    ), {
        "root": cli.Path("."),
        "font_paths": [cli.Path("fonts")],
        "strict_fonts": True,
        "keep_pdf": False,
    })]
    assert capsys.readouterr().out == f"{output}\n"
