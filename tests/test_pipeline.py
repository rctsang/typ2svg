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


def test_mutool_convert_returns_numbered_svg_for_file_output(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    pdf = tmp_path / "input.pdf"
    pdf.write_text("pdf")
    output = tmp_path / "output.svg"
    numbered_output = tmp_path / "output1.svg"

    def run(command, capture_output, text):
        numbered_output.write_text("<svg />")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(pipeline.subprocess, "run", run)

    assert pipeline.mutool_convert(pdf, output) == [numbered_output]


def test_match_font_variant_accepts_compacted_pdf_font_name(monkeypatch, tmp_path):
    fonts = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.fonts")
    variant = fonts.FontVariant(
        family="DejaVu Sans",
        location="/tmp/font.ttf",
        style="Normal",
        weight="400",
        stretch="100%",
        variable=False,
    )

    assert fonts.match_font_variant(fonts.FontDependency("DejaVuSans"), [variant]) == variant
