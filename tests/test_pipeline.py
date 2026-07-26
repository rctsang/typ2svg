from __future__ import annotations

import importlib
import os
import subprocess
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


class FakeRect:
    def __init__(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1


class FakePage:
    def __init__(
        self,
        svg: str,
        *,
        links: list[dict] | None = None,
        rawdict: dict | None = None,
    ) -> None:
        self.svg = svg
        self.links = links or []
        self.rawdict = rawdict or {"blocks": []}
        self.text_as_path = None

    def get_svg_image(self, text_as_path=True):
        self.text_as_path = text_as_path
        return self.svg

    def get_links(self):
        return self.links

    def get_text(self, variant):
        assert variant == "rawdict"
        return self.rawdict


class FakeDocument:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def __iter__(self):
        return iter(self.pages)


def test_mupdf_convert_writes_single_combined_svg(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    pdf = tmp_path / "input.pdf"
    pdf.write_text("pdf")
    output = tmp_path / "output.svg"
    pages = [
        FakePage('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40"><text>one</text></svg>'),
        FakePage('<svg xmlns="http://www.w3.org/2000/svg" width="80" height="50"><text>two</text></svg>'),
    ]
    document = FakeDocument(pages)

    monkeypatch.setattr(pipeline.pymupdf, "open", lambda path: document)

    assert pipeline.mupdf_convert(pdf, output) == [output]

    tree = pipeline.ETree.parse(output)
    root = tree.getroot()
    nested = list(root)
    assert root.get("width") == "100"
    assert root.get("height") == "90"
    assert root.get("viewBox") == "0 0 100 90"
    assert [page.get("y") for page in nested] == ["0", "40"]
    assert [page.text_as_path for page in pages] == [False, False]


def test_mupdf_convert_writes_pdf_stem_svg_for_directory_output(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    pdf = tmp_path / "input.pdf"
    pdf.write_text("pdf")
    output_dir = tmp_path / "svgs"
    document = FakeDocument([
        FakePage('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40" />'),
    ])

    monkeypatch.setattr(pipeline.pymupdf, "open", lambda path: document)

    assert pipeline.mupdf_convert(pdf, output_dir) == [output_dir / "input.svg"]


def test_mupdf_convert_prefixes_page_ids(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    pdf = tmp_path / "input.pdf"
    pdf.write_text("pdf")
    output = tmp_path / "output.svg"
    document = FakeDocument([
        FakePage('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40"><clipPath id="clip"><path /></clipPath><g clip-path="url(#clip)" /></svg>'),
        FakePage('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40"><clipPath id="clip"><path /></clipPath><g clip-path="url(#clip)" /></svg>'),
    ])

    monkeypatch.setattr(pipeline.pymupdf, "open", lambda path: document)

    pipeline.mupdf_convert(pdf, output)

    text = output.read_text()
    assert 'id="p1-clip"' in text
    assert 'clip-path="url(#p1-clip)"' in text
    assert 'id="p2-clip"' in text
    assert 'clip-path="url(#p2-clip)"' in text


def test_mupdf_convert_wraps_uri_link_text(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    pdf = tmp_path / "input.pdf"
    pdf.write_text("pdf")
    output = tmp_path / "output.svg"
    rawdict = {
        "blocks": [{
            "lines": [{
                "spans": [{
                    "chars": [
                        {"c": "E", "bbox": (10, 10, 16, 20)},
                        {"c": "x", "bbox": (16, 10, 22, 20)},
                        {"c": "a", "bbox": (22, 10, 28, 20)},
                        {"c": "m", "bbox": (28, 10, 34, 20)},
                        {"c": "p", "bbox": (34, 10, 40, 20)},
                        {"c": "l", "bbox": (40, 10, 46, 20)},
                        {"c": "e", "bbox": (46, 10, 52, 20)},
                    ],
                }],
            }],
        }],
    }
    document = FakeDocument([
        FakePage(
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40"><text><tspan x="10 16 22 28 34 40 46" y="20">Example</tspan></text></svg>',
            links=[{
                "kind": pipeline.pymupdf.LINK_URI,
                "from": FakeRect(10, 10, 52, 20),
                "uri": "https://example.com",
            }],
            rawdict=rawdict,
        ),
    ])

    monkeypatch.setattr(pipeline.pymupdf, "open", lambda path: document)

    pipeline.mupdf_convert(pdf, output)

    text = output.read_text()
    assert '<a href="https://example.com"' in text
    assert ">Example</" in text
    assert "pointer-events" not in text


def test_mupdf_convert_uses_fragment_for_internal_page_link(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    pdf = tmp_path / "input.pdf"
    pdf.write_text("pdf")
    output = tmp_path / "output.svg"
    document = FakeDocument([
        FakePage(
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40"><text>one</text></svg>',
            links=[{
                "kind": pipeline.pymupdf.LINK_GOTO,
                "from": FakeRect(10, 10, 30, 20),
                "page": 1,
            }],
        ),
        FakePage('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40"><text>two</text></svg>'),
    ])

    monkeypatch.setattr(pipeline.pymupdf, "open", lambda path: document)

    pipeline.mupdf_convert(pdf, output)

    text = output.read_text()
    assert 'id="page-1"' in text
    assert 'id="page-2"' in text
    assert 'href="#page-2"' in text
    assert '<rect x="10" y="10" width="20" height="10"' in text


def test_match_font_variant_accepts_font_metadata_alias(monkeypatch, tmp_path):
    fonts = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.fonts")
    variant = fonts.FontVariant(
        family="DejaVu Sans",
        location="/tmp/font.ttf",
        style="Normal",
        weight="400",
        stretch="100%",
        variable=False,
    )
    monkeypatch.setattr(
        fonts,
        "font_aliases",
        lambda matched: {"DejaVu Sans", "DejaVuSans"},
    )

    assert fonts.match_font_variant(fonts.FontDependency("DejaVuSans"), [variant]) == variant
