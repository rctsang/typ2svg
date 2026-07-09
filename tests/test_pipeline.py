from __future__ import annotations

import importlib
import os
import subprocess
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


def test_compile_typst_builds_command(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    calls: list[list[str]] = []

    def run(command, capture_output, text):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(pipeline.subprocess, "run", run)

    pipeline.compile_typst(
        "input.typ",
        "output.pdf",
        root=".",
        font_paths=["fonts"],
    )

    assert calls == [[
        "typst",
        "compile",
        "--root",
        ".",
        "--font-path",
        "fonts",
        "input.typ",
        "output.pdf",
    ]]


def test_mutool_convert_returns_existing_output(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    pdf = tmp_path / "input.pdf"
    pdf.write_text("pdf")
    output = tmp_path / "output.svg"

    def run(command, capture_output, text):
        output.write_text("<svg />")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(pipeline.subprocess, "run", run)

    assert pipeline.mutool_convert(pdf, output) == [output]
