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


def test_mupdf_convert_supports_safe_launch_fragment_links(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    pdf = tmp_path / "input.pdf"
    pdf.write_text("pdf")
    output = tmp_path / "output.svg"
    rawdict = {
        "blocks": [{
            "lines": [{
                "spans": [{
                    "chars": [
                        {"c": "O", "bbox": (10, 10, 20, 30)},
                        {"c": "l", "bbox": (20, 10, 30, 30)},
                        {"c": "d", "bbox": (30, 10, 40, 30)},
                        {"c": "N", "bbox": (10, 30, 20, 50)},
                        {"c": "e", "bbox": (20, 30, 30, 50)},
                        {"c": "w", "bbox": (30, 30, 40, 50)},
                    ],
                }],
            }],
        }],
    }
    document = FakeDocument([
        FakePage(
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80"><text><tspan x="10 20 30" y="20">Old</tspan><tspan x="10 20 30" y="40">New</tspan></text></svg>',
            links=[{
                "kind": pipeline.pymupdf.LINK_LAUNCH,
                "from": FakeRect(10, 30, 40, 50),
                "file": "#",
            }],
            rawdict=rawdict,
        ),
    ])

    monkeypatch.setattr(pipeline.pymupdf, "open", lambda path: document)

    pipeline.mupdf_convert(pdf, output)

    text = output.read_text()
    assert '<a href="#"' in text
    assert ">New</" in text
    assert ">Old</" in text


def test_mupdf_convert_uses_svg_transforms_to_choose_link_text(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    pdf = tmp_path / "input.pdf"
    pdf.write_text("pdf")
    output = tmp_path / "output.svg"
    rawdict = {
        "blocks": [{
            "lines": [{
                "spans": [{
                    "chars": [
                        {"c": "S", "bbox": (10, 90, 20, 110)},
                        {"c": "a", "bbox": (20, 90, 30, 110)},
                        {"c": "m", "bbox": (30, 90, 40, 110)},
                        {"c": "e", "bbox": (40, 90, 50, 110)},
                    ],
                }],
            }],
        }],
    }
    document = FakeDocument([
        FakePage(
            '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"><text transform="matrix(1 0 0 1 10 20)"><tspan x="0 10 20 30" y="0">Same</tspan></text><text transform="matrix(1 0 0 1 10 100)"><tspan x="0 10 20 30" y="0">Same</tspan></text></svg>',
            links=[{
                "kind": pipeline.pymupdf.LINK_URI,
                "from": FakeRect(10, 90, 50, 110),
                "uri": "https://example.com",
            }],
            rawdict=rawdict,
        ),
    ])

    monkeypatch.setattr(pipeline.pymupdf, "open", lambda path: document)

    pipeline.mupdf_convert(pdf, output)

    root = pipeline.ETree.parse(output).getroot()
    parents = {child: parent for parent in root.iter() for child in parent}
    anchors = [
        element for element in root.iter(f"{{{pipeline.SVG_NAMESPACE}}}a")
        if element.get("href") == "https://example.com"
    ]
    linked_tspan = list(anchors[0])[0]
    linked_text = parents[linked_tspan]
    while linked_text.tag.rsplit("}", 1)[-1] != "text":
        linked_text = parents[linked_text]
    assert linked_text.get("transform") == "matrix(1 0 0 1 10 100)"


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


def _single_line_rawdict(text: str, x0: float, y0: float, step: float, height: float) -> dict:
    return {
        "blocks": [{
            "lines": [{
                "spans": [{
                    "chars": [
                        {"c": c, "bbox": (x0 + i * step, y0, x0 + (i + 1) * step, y0 + height)}
                        for i, c in enumerate(text)
                    ],
                }],
            }],
        }],
    }


def test_mupdf_convert_drops_unsafe_uri_links(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    pdf = tmp_path / "input.pdf"
    pdf.write_text("pdf")
    output = tmp_path / "output.svg"
    document = FakeDocument([
        FakePage(
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40"><text><tspan x="10 16 22" y="20">Bad</tspan></text></svg>',
            links=[
                {"kind": pipeline.pymupdf.LINK_URI, "from": FakeRect(10, 10, 28, 20), "uri": "javascript:alert(1)"},
                {"kind": pipeline.pymupdf.LINK_URI, "from": FakeRect(10, 10, 28, 20), "uri": " JavaScript:alert(1)"},
                {"kind": pipeline.pymupdf.LINK_LAUNCH, "from": FakeRect(10, 10, 28, 20), "file": "/etc/passwd"},
            ],
            rawdict=_single_line_rawdict("Bad", 10, 10, 6, 10),
        ),
    ])
    monkeypatch.setattr(pipeline.pymupdf, "open", lambda path: document)

    pipeline.mupdf_convert(pdf, output)

    text = output.read_text()
    assert "javascript" not in text.lower()
    assert "passwd" not in text
    assert "<a " not in text


def test_safe_href_allowlist(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    assert pipeline._safe_href("HTTPS://example.com") == "HTTPS://example.com"
    assert pipeline._safe_href("mailto:a@b.c") == "mailto:a@b.c"
    assert pipeline._safe_href("#page-2") == "#page-2"
    assert pipeline._safe_href("data:text/html,x") is None
    assert pipeline._safe_href("file:///etc/passwd") is None
    assert pipeline._safe_href(None) is None


def test_link_text_outside_rect_falls_back_to_rect(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    pdf = tmp_path / "input.pdf"
    pdf.write_text("pdf")
    output = tmp_path / "output.svg"
    # The PDF says "here" is at y=100, but the only SVG "here" sits at y=20.
    document = FakeDocument([
        FakePage(
            '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"><text><tspan x="10 16 22 28" y="20">here</tspan></text></svg>',
            links=[{"kind": pipeline.pymupdf.LINK_URI, "from": FakeRect(10, 90, 34, 110), "uri": "https://example.com"}],
            rawdict=_single_line_rawdict("here", 10, 90, 6, 20),
        ),
    ])
    monkeypatch.setattr(pipeline.pymupdf, "open", lambda path: document)

    pipeline.mupdf_convert(pdf, output)

    root = pipeline.ETree.parse(output).getroot()
    anchors = list(root.iter(f"{{{pipeline.SVG_NAMESPACE}}}a"))
    assert len(anchors) == 1
    assert [pipeline._local_name(child.tag) for child in anchors[0]] == ["rect"]


def test_link_does_not_rewrap_already_linked_text(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    root = pipeline.ETree.fromstring(
        '<svg xmlns="http://www.w3.org/2000/svg"><text><tspan x="10 16 22 28" y="20">here</tspan></text></svg>'
    )
    link = pipeline.PdfLink("https://a.example", (10, 10, 34, 22), "here")
    other = pipeline.PdfLink("https://b.example", (10, 10, 34, 22), "here")

    pipeline._add_links(root, [link, other])

    anchors = list(root.iter(f"{{{pipeline.SVG_NAMESPACE}}}a"))
    assert [a.get("href") for a in anchors] == ["https://a.example", "https://b.example"]
    for anchor in anchors:
        assert not list(anchor.iter(f"{{{pipeline.SVG_NAMESPACE}}}a"))[1:]


def test_wrap_keeps_position_and_unique_ids_without_per_char_x(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    root = pipeline.ETree.fromstring(
        '<svg xmlns="http://www.w3.org/2000/svg"><text><tspan id="t" x="5" y="20">ab</tspan></text></svg>'
    )
    tspan = next(root.iter(f"{{{pipeline.SVG_NAMESPACE}}}tspan"))

    pipeline._wrap_text_element(pipeline._parent_map(root), tspan, 1, 2, "#x")

    tspans = list(root.iter(f"{{{pipeline.SVG_NAMESPACE}}}tspan"))
    assert [t.text for t in tspans] == ["a", "b"]
    assert tspans[0].get("x") == "5"
    assert tspans[1].get("x") is None
    assert [t.get("id") for t in tspans] == ["t", None]


def test_prefix_svg_ids_rewrites_references(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    root = pipeline.ETree.fromstring(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<clipPath id="c"/><g clip-path="url(\'#c\')" fill="url(#c) none"/>'
        '<use xlink:href="#c"/><use href="#other"/></svg>'
    )

    pipeline._prefix_svg_ids(root, "p1-")

    text = pipeline.ETree.tostring(root, encoding="unicode")
    assert 'id="p1-c"' in text
    assert "clip-path=\"url('#p1-c')\"" in text
    assert 'fill="url(#p1-c) none"' in text
    assert 'xlink:href="#p1-c"' in text
    assert 'href="#other"' in text


def test_resolve_svg_path_is_shared_by_keep_pdf(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    dotted_dir = tmp_path / "build" / "v0.1"

    svg = pipeline._resolve_svg_path("doc", dotted_dir)

    assert svg == dotted_dir / "doc.svg"
    assert dotted_dir.is_dir()
    assert pipeline._resolve_svg_path("doc", tmp_path / "out.SVG") == tmp_path / "out.SVG"


def test_parse_transform_supports_scale_and_rotate(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    x, y = pipeline._transform_point(pipeline._parse_transform("translate(10 5) scale(2)"), 1, 1)
    assert (x, y) == (12, 7)
    x, y = pipeline._transform_point(pipeline._parse_transform("rotate(90)"), 1, 0)
    assert abs(x) < 1e-9 and abs(y - 1) < 1e-9


def test_match_font_variant_treats_oblique_as_slanted(monkeypatch, tmp_path):
    fonts = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.fonts")
    normal = fonts.FontVariant("Foo", "/f/a.ttf", "Normal", "400", "100%", False)
    oblique = fonts.FontVariant("Foo", "/f/b.ttf", "Oblique", "400", "100%", False)

    assert fonts.match_font_variant(fonts.FontDependency("Foo", "italic"), [normal, oblique]) == oblique
    assert fonts.match_font_variant(fonts.FontDependency("Foo", "oblique"), [normal, oblique]) == oblique


def test_get_fonts_passes_font_paths(monkeypatch, tmp_path):
    fonts = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.fonts")
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 0, "Foo\n  └ /f/foo.ttf\n      Style: Normal, Weight: 400, Stretch: 100%\n", ""
        )

    monkeypatch.setattr(fonts.subprocess, "run", run)
    fonts._get_fonts.cache_clear()
    try:
        variants = fonts.get_fonts([tmp_path / "fonts"])
    finally:
        fonts._get_fonts.cache_clear()

    assert calls == [["typst", "fonts", "--variants", "--font-path", str(tmp_path / "fonts")]]
    assert variants == [fonts.FontVariant("Foo", "/f/foo.ttf", "Normal", "400", "100%", False)]


def test_missing_typst_raises_clear_error(monkeypatch, tmp_path):
    pipeline = import_typ2svg_module(monkeypatch, tmp_path, "typ2svg.pipeline")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    try:
        pipeline.compile_typst("a.typ", "a.pdf")
    except FileNotFoundError as error:
        assert "typst" in str(error)
    else:
        raise AssertionError("expected FileNotFoundError")
