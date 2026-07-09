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


def test_get_font_dependencies_reads_attributes_and_inline_styles(monkeypatch, tmp_path):
    svg = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.svg")
    path = tmp_path / "input.svg"
    path.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg">
  <text font-family="'DejaVu Sans Mono', monospace" font-weight="700">A</text>
  <text style="font-family: &quot;Fira Code&quot;, sans-serif; font-style: italic">B</text>
</svg>"""
    )

    assert svg.get_font_dependencies(path) == [
        svg.FontDependency("DejaVu Sans Mono", None, "700", None),
        svg.FontDependency("Fira Code", "italic", None, None),
    ]


def test_embed_fonts_inserts_font_face_rule(monkeypatch, tmp_path):
    svg = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.svg")
    fonts = importlib.import_module("typ2svg.fonts")
    path = tmp_path / "input.svg"
    path.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg">
  <text font-family="DejaVu Sans Mono">A</text>
</svg>"""
    )
    variant = fonts.FontVariant(
        family="DejaVu Sans Mono",
        location="/tmp/font.ttf",
        style="Normal",
        weight="400",
        stretch="100%",
        variable=False,
    )
    monkeypatch.setattr(svg, "match_font_variant", lambda dependency: variant)
    monkeypatch.setattr(svg, "encode_variant", lambda matched: "Zm9udA==")

    svg.embed_fonts(path)

    output = path.read_text()
    assert "@font-face" in output
    assert 'font-family: "DejaVu Sans Mono";' in output
    assert 'data:font/woff2;base64,Zm9udA==' in output
