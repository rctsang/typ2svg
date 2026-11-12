from __future__ import annotations

import importlib
import os
import sys


def import_typ2svg_module(monkeypatch, tmp_path, name: str):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for command in ("typst",):
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
    monkeypatch.setattr(svg, "match_font_variant", lambda dependency, **kwargs: variant)
    monkeypatch.setattr(svg, "encode_variant", lambda matched, text=None: "Zm9udA==")

    svg.embed_fonts(path)

    output = path.read_text()
    assert "@font-face" in output
    assert 'font-family: "DejaVu Sans Mono";' in output
    assert 'data:font/woff2;base64,Zm9udA==' in output


def test_embed_fonts_uses_svg_font_family_name(monkeypatch, tmp_path):
    svg = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.svg")
    fonts = importlib.import_module("typ2svg.fonts")
    path = tmp_path / "input.svg"
    path.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg">
  <text font-family="DejaVuSans">A</text>
</svg>"""
    )
    variant = fonts.FontVariant(
        family="DejaVu Sans",
        location="/tmp/font.ttf",
        style="Normal",
        weight="400",
        stretch="100%",
        variable=False,
    )
    monkeypatch.setattr(svg, "match_font_variant", lambda dependency, **kwargs: variant)
    monkeypatch.setattr(svg, "encode_variant", lambda matched, text=None: "Zm9udA==")

    svg.embed_fonts(path)

    output = path.read_text()
    assert 'font-family: "DejaVuSans";' in output


def test_embed_fonts_subsets_to_used_characters(monkeypatch, tmp_path):
    svg = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.svg")
    fonts = importlib.import_module("typ2svg.fonts")
    path = tmp_path / "input.svg"
    path.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg">
  <text font-family="Foo"><tspan>ab</tspan></text>
  <text font-family="Foo"><tspan>bc</tspan></text>
</svg>"""
    )
    variant = fonts.FontVariant("Foo", "/tmp/font.ttf", "Normal", "400", "100%", False)
    texts = []
    monkeypatch.setattr(svg, "match_font_variant", lambda dependency, **kwargs: variant)
    monkeypatch.setattr(svg, "encode_variant", lambda matched, text=None: texts.append(text) or "Zm9udA==")

    svg.embed_fonts(path)

    assert texts == ["abc"]
    assert path.read_text().count("@font-face") == 1


def _make_collection(tmp_path):
    from fontTools.ttLib import TTCollection, TTFont

    fonts = importlib.import_module("typ2svg.fonts")
    assets = importlib.import_module("typ2svg").ASSETS
    faces = []
    for name in ("DejaVuSansMono.woff2", "Excalifont-Regular.woff2"):
        font = TTFont(str(assets.joinpath(name)))
        font.flavor = None
        faces.append(font)
    collection = TTCollection()
    collection.fonts = faces
    path = tmp_path / "pair.ttc"
    collection.save(str(path))
    fonts._font_faces.cache_clear()
    return fonts, path


def test_font_collection_face_is_matched_and_embedded(monkeypatch, tmp_path, capfd):
    import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.svg")
    fonts, path = _make_collection(tmp_path)
    variant = fonts.FontVariant("Excalifont", str(path), "Normal", "400", "100%", False)

    assert fonts._face_index(variant) == 1
    assert "Excalifont" in fonts.font_aliases(variant)
    assert fonts.encode_variant(variant, "Hi")
    assert capfd.readouterr().err == ""


def test_unreadable_font_is_skipped_silently(monkeypatch, tmp_path, capfd):
    fonts = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.fonts")
    broken = tmp_path / "broken.ttc"
    broken.write_bytes(b"ttcf not really a font")
    fonts._font_faces.cache_clear()
    variant = fonts.FontVariant("Broken", str(broken), "Normal", "400", "100%", False)

    assert fonts.match_font_variant(fonts.FontDependency("Missing"), [variant]) is None
    assert fonts.font_aliases(variant) == {"Broken"}
    captured = capfd.readouterr()
    assert captured.err == "" and captured.out == ""
