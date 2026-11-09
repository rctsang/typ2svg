"""End-to-end test using the real typst and PyMuPDF (skipped without typst)."""
from __future__ import annotations

import shutil
import xml.etree.ElementTree as ETree

import pytest

pytestmark = pytest.mark.skipif(shutil.which("typst") is None, reason="typst not installed")

SVG = "{http://www.w3.org/2000/svg}"

SOURCE = """\
#set page(width: 300pt, height: 200pt)
Hello #link("javascript:alert(1)")[bad] and #link("https://example.com")[here].

#text(weight: "bold")[Bold] text, #link(<two>)[go] there.

#pagebreak()
= Second <two>
"""


def test_typ2svg_end_to_end(tmp_path):
    from typ2svg import typ2svg

    src = tmp_path / "doc.typ"
    src.write_text(SOURCE)
    result = typ2svg(src, tmp_path / "out", keep_pdf=True)

    svg = tmp_path / "out" / "doc.svg"
    assert result.svgs == [svg]
    assert result.pdf == tmp_path / "out" / "doc.pdf"
    assert result.pdf.exists()

    text = svg.read_text()
    root = ETree.fromstring(text)
    assert "javascript" not in text.lower()

    hrefs = {a.get("href"): "".join(a.itertext()) for a in root.iter(f"{SVG}a")}
    assert hrefs.get("https://example.com") == "here"
    assert "#page-2" in hrefs

    ids = [element.get("id") for element in root.iter() if element.get("id")]
    assert len(ids) == len(set(ids))
    assert {"page-1", "page-2"} <= set(ids)

    if "@font-face" in text:
        # Subset fonts should stay small for a tiny document.
        assert svg.stat().st_size < 200_000
