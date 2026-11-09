from __future__ import annotations

import math
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ETree
from pathlib import Path
from typing import Iterable, NamedTuple, TypeAlias

import pymupdf

from .fonts import require_program
from .svg import embed_fonts_tree

PathLike: TypeAlias = str | Path
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
INKSCAPE_NAMESPACE = "http://www.inkscape.org/namespaces/inkscape"
TRANSFORM_RE = re.compile(r"(matrix|translate|scale|rotate)\(([^)]*)\)")
URL_REF_RE = re.compile(r"""url\((['"]?)#([^'")\s]+)\1\)""")
SAFE_HREF_PREFIXES = ("#", "http://", "https://", "mailto:")
# How far (in PDF points) a matched text center may fall outside a link rect.
LINK_RECT_TOLERANCE = 2.0
Matrix: TypeAlias = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

ETree.register_namespace("", SVG_NAMESPACE)
ETree.register_namespace("xlink", XLINK_NAMESPACE)
ETree.register_namespace("inkscape", INKSCAPE_NAMESPACE)


class CommandError(RuntimeError):
    def __init__(self, command: list[str], stderr: str) -> None:
        super().__init__(
            "command failed: {}\n{}".format(" ".join(command), stderr.strip())
        )
        self.command = command
        self.stderr = stderr


class Typ2SvgResult(NamedTuple):
    pdf: Path
    svgs: list[Path]


class PdfLink(NamedTuple):
    href: str
    rect: tuple[float, float, float, float]
    text: str


class PageSvg(NamedTuple):
    svg: str
    links: list[PdfLink]


def _run(command: list[str]) -> None:
    require_program(command[0])
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise CommandError(command, result.stderr)


def compile_typst(
    src: PathLike,
    pdf: PathLike,
    *,
    root: PathLike | None = None,
    font_paths: Iterable[PathLike] | None = None,
) -> Path:
    src = Path(src)
    pdf = Path(pdf)
    command = ["typst", "compile"]
    if root is not None:
        command.extend(["--root", str(root)])
    for font_path in font_paths or []:
        command.extend(["--font-path", str(font_path)])
    command.extend([str(src), str(pdf)])
    _run(command)
    return pdf


def _length_attr(element: ETree.Element, name: str) -> tuple[float, str]:
    """Return an SVG length attribute as ``(number, unit)``."""
    value = element.get(name)
    if value is None:
        raise ValueError(f"SVG page is missing {name!r}")
    value = value.strip()
    for suffix in ("pt", "px"):
        if value.endswith(suffix):
            return float(value.removesuffix(suffix)), suffix
    return float(value), ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parent_map(root: ETree.Element) -> dict[ETree.Element, ETree.Element]:
    return {child: parent for parent in root.iter() for child in parent}


def _rect_tuple(rect) -> tuple[float, float, float, float]:
    return (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))


def _contains_point(
    rect: tuple[float, float, float, float],
    x: float,
    y: float,
    tolerance: float = 0.0,
) -> bool:
    return (
        rect[0] - tolerance <= x <= rect[2] + tolerance
        and rect[1] - tolerance <= y <= rect[3] + tolerance
    )


def _page_chars(page) -> list[tuple[str, float, float]]:
    """Return every character on the page with its bbox center point."""
    get_text = getattr(page, "get_text", None)
    if get_text is None:
        return []

    chars: list[tuple[str, float, float]] = []
    for block in get_text("rawdict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    x0, y0, x1, y1 = (
                        float(value) for value in char.get("bbox", (0, 0, 0, 0))
                    )
                    chars.append((char.get("c", ""), (x0 + x1) / 2, (y0 + y1) / 2))
    return chars


def _linked_text(
    chars: Iterable[tuple[str, float, float]],
    rect: tuple[float, float, float, float],
) -> str:
    return "".join(
        char for char, cx, cy in chars if _contains_point(rect, cx, cy)
    ).strip()


def _safe_href(href: object) -> str | None:
    """Return ``href`` only if it uses an allowlisted scheme (or is a fragment)."""
    if not isinstance(href, str):
        return None
    href = href.strip()
    if href.lower().startswith(SAFE_HREF_PREFIXES):
        return href
    return None


def _extract_links(page, page_count: int) -> list[PdfLink]:
    get_links = getattr(page, "get_links", None)
    if get_links is None:
        return []

    raw_links = list(get_links())
    if not raw_links:
        return []

    chars = _page_chars(page)
    links: list[PdfLink] = []
    for link in raw_links:
        rect = _rect_tuple(link["from"])
        kind = link.get("kind")
        href: str | None = None
        if kind == pymupdf.LINK_URI:
            href = _safe_href(link.get("uri"))
        elif kind in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED):
            # Typst label links are named destinations; PyMuPDF resolves the page.
            target_page = link.get("page")
            if isinstance(target_page, int) and 0 <= target_page < page_count:
                href = f"#page-{target_page + 1}"
        elif kind == pymupdf.LINK_LAUNCH:
            href = _safe_href(link.get("file"))
        if href:
            links.append(PdfLink(href, rect, _linked_text(chars, rect)))
    return links


def _prefix_svg_ids(root: ETree.Element, prefix: str) -> None:
    id_map = {
        value: f"{prefix}{value}"
        for element in root.iter()
        if (value := element.get("id"))
    }
    if not id_map:
        return

    def replace_url(match: re.Match[str]) -> str:
        quote, ref = match.groups()
        return f"url({quote}#{id_map.get(ref, ref)}{quote})"

    for element in root.iter():
        for name, value in list(element.attrib.items()):
            if name == "id":
                updated = id_map.get(value, value)
            elif value.startswith("#") and value[1:] in id_map:
                updated = f"#{id_map[value[1:]]}"
            elif "url(" in value:
                updated = URL_REF_RE.sub(replace_url, value)
            else:
                continue
            if updated != value:
                element.set(name, updated)


def _split_coordinates(value: str | None) -> list[str] | None:
    if value is None:
        return None
    coordinates = value.replace(",", " ").split()
    return coordinates or None


def _parse_transform_numbers(value: str) -> list[float]:
    return [float(number) for number in re.split(r"[\s,]+", value.strip()) if number]


def _multiply_matrix(first: Matrix, second: Matrix) -> Matrix:
    a1, b1, c1, d1, e1, f1 = first
    a2, b2, c2, d2, e2, f2 = second
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _parse_transform(value: str | None) -> Matrix:
    """Parse the subset of SVG transforms mupdf emits (skewX/skewY are ignored)."""
    matrix = IDENTITY
    if not value:
        return matrix

    for name, arguments in TRANSFORM_RE.findall(value):
        numbers = _parse_transform_numbers(arguments)
        transform: Matrix
        if name == "matrix" and len(numbers) == 6:
            transform = (
                numbers[0], numbers[1], numbers[2], numbers[3], numbers[4], numbers[5]
            )
        elif name == "translate" and numbers:
            ty = numbers[1] if len(numbers) > 1 else 0.0
            transform = (1.0, 0.0, 0.0, 1.0, numbers[0], ty)
        elif name == "scale" and numbers:
            sy = numbers[1] if len(numbers) > 1 else numbers[0]
            transform = (numbers[0], 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate" and numbers:
            angle = math.radians(numbers[0])
            cos, sin = math.cos(angle), math.sin(angle)
            transform = (cos, sin, -sin, cos, 0.0, 0.0)
            if len(numbers) == 3:
                cx, cy = numbers[1], numbers[2]
                transform = _multiply_matrix(
                    _multiply_matrix((1.0, 0.0, 0.0, 1.0, cx, cy), transform),
                    (1.0, 0.0, 0.0, 1.0, -cx, -cy),
                )
        else:
            continue
        matrix = _multiply_matrix(matrix, transform)
    return matrix


def _ancestors(
    element: ETree.Element,
    parents: dict[ETree.Element, ETree.Element],
) -> list[ETree.Element]:
    chain: list[ETree.Element] = []
    while element in parents:
        element = parents[element]
        chain.append(element)
    return chain


def _element_transform(
    element: ETree.Element,
    parents: dict[ETree.Element, ETree.Element],
) -> Matrix:
    matrix = IDENTITY
    for item in reversed([element, *_ancestors(element, parents)]):
        matrix = _multiply_matrix(matrix, _parse_transform(item.get("transform")))
    return matrix


def _transform_point(matrix: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return (a * x + c * y + e, b * x + d * y + f)


def _range_end_x(coordinates: list[float], end: int) -> float:
    if end < len(coordinates):
        return coordinates[end]
    if len(coordinates) > 1:
        return coordinates[-1] + coordinates[-1] - coordinates[-2]
    return coordinates[-1]


def _copy_text_element(
    element: ETree.Element,
    text: str,
    x: list[str] | None,
    *,
    first: bool,
) -> ETree.Element:
    """Copy a text element for one segment of a split.

    ``x`` holds per-character coordinates for the segment when available.
    Without them, only the first segment keeps the original position
    attributes so that later segments flow on from it; only the first
    segment keeps the original ``id`` so IDs stay unique.
    """
    copied = ETree.Element(element.tag, element.attrib)
    copied.text = text
    if x is not None:
        copied.set("x", " ".join(x))
    elif not first:
        copied.attrib.pop("x", None)
    if not first:
        copied.attrib.pop("id", None)
    return copied


def _link_element(href: str) -> ETree.Element:
    link = ETree.Element(f"{{{SVG_NAMESPACE}}}a")
    link.set("href", href)
    link.set(f"{{{XLINK_NAMESPACE}}}href", href)
    return link


def _wrap_text_element(
    parents: dict[ETree.Element, ETree.Element],
    element: ETree.Element,
    start: int,
    end: int,
    href: str,
) -> None:
    """Split ``element`` and wrap ``text[start:end]`` in an ``<a>``.

    ``parents`` is updated in place so it stays valid for later links.
    """
    parent = parents[element]
    child_index = list(parent).index(element)
    text = element.text or ""
    coordinates = _split_coordinates(element.get("x"))
    split_x = (
        coordinates
        if coordinates is not None and len(coordinates) >= len(text)
        else None
    )
    segments = [
        (seg_start, seg_end, seg_start == start)
        for seg_start, seg_end in ((0, start), (start, end), (end, len(text)))
        if seg_end > seg_start
    ]
    replacements: list[ETree.Element] = []
    for index, (seg_start, seg_end, is_link) in enumerate(segments):
        copied = _copy_text_element(
            element,
            text[seg_start:seg_end],
            split_x[seg_start:seg_end] if split_x else None,
            first=index == 0,
        )
        if is_link:
            link = _link_element(href)
            link.append(copied)
            parents[copied] = link
            copied = link
        parents[copied] = parent
        replacements.append(copied)

    replacements[-1].tail = element.tail
    parent.remove(element)
    del parents[element]
    for offset, replacement in enumerate(replacements):
        parent.insert(child_index + offset, replacement)


def _text_center_score(
    parents: dict[ETree.Element, ETree.Element],
    element: ETree.Element,
    start: int,
    end: int,
    rect: tuple[float, float, float, float],
) -> float | None:
    """Distance from the matched text's center to the link rect's center.

    Returns ``None`` when the position is unknown or the text center lies
    outside the link rect, so the match must not be used.
    """
    coordinates = [
        float(value) for value in _split_coordinates(element.get("x")) or []
    ]
    y = _split_coordinates(element.get("y"))
    if y is None or start >= len(coordinates):
        return None
    x0 = coordinates[start]
    x1 = _range_end_x(coordinates, end)
    cx, cy = _transform_point(
        _element_transform(element, parents),
        (x0 + x1) / 2,
        float(y[0]),
    )
    if not _contains_point(rect, cx, cy, LINK_RECT_TOLERANCE):
        return None
    rx = (rect[0] + rect[2]) / 2
    ry = (rect[1] + rect[3]) / 2
    return abs(cx - rx) + abs(cy - ry)


def _inside_link(
    element: ETree.Element,
    parents: dict[ETree.Element, ETree.Element],
) -> bool:
    return any(_local_name(item.tag) == "a" for item in _ancestors(element, parents))


def _wrap_link_text(
    root: ETree.Element,
    parents: dict[ETree.Element, ETree.Element],
    link: PdfLink,
) -> bool:
    if not link.text:
        return False

    candidates: list[tuple[float, ETree.Element, int, int]] = []
    for element in root.iter():
        if _local_name(element.tag) != "tspan" or not element.text:
            continue
        start = element.text.find(link.text)
        if start < 0 or _inside_link(element, parents):
            continue
        while start >= 0:
            end = start + len(link.text)
            score = _text_center_score(parents, element, start, end, link.rect)
            if score is not None:
                candidates.append((score, element, start, end))
            start = element.text.find(link.text, start + 1)

    if not candidates:
        return False

    _, element, start, end = min(candidates, key=lambda candidate: candidate[0])
    _wrap_text_element(parents, element, start, end, link.href)
    return True


def _add_link_rect(root: ETree.Element, link: PdfLink) -> None:
    x0, y0, x1, y1 = link.rect
    anchor = _link_element(link.href)
    rect = ETree.Element(f"{{{SVG_NAMESPACE}}}rect", {
        "x": f"{x0:g}",
        "y": f"{y0:g}",
        "width": f"{x1 - x0:g}",
        "height": f"{y1 - y0:g}",
        "fill": "transparent",
        "opacity": "0",
        "pointer-events": "all",
    })
    anchor.append(rect)
    root.append(anchor)


def _add_links(root: ETree.Element, links: Iterable[PdfLink]) -> None:
    parents = _parent_map(root)
    for link in links:
        if not _wrap_link_text(root, parents, link):
            _add_link_rect(root, link)


def _combine_page_svgs(page_svgs: Iterable[PageSvg]) -> ETree.ElementTree:
    page_data = [(ETree.fromstring(page.svg), page.links) for page in page_svgs]
    pages = [page for page, _ in page_data]
    if not pages:
        raise ValueError("PDF has no pages")

    lengths = [(_length_attr(page, "width"), _length_attr(page, "height")) for page in pages]
    sizes = [(page_width, page_height) for (page_width, _), (page_height, _) in lengths]
    units = {unit for size in lengths for _, unit in size}
    # Nested pages are positioned in the outer viewBox's user units; only the
    # outermost width/height carries the physical unit, if the pages agree on one.
    unit = units.pop() if len(units) == 1 else ""
    width = max(page_width for page_width, _ in sizes)
    height = sum(page_height for _, page_height in sizes)
    root = ETree.Element(f"{{{SVG_NAMESPACE}}}svg", {
        "version": "1.1",
        "width": f"{width:g}{unit}",
        "height": f"{height:g}{unit}",
        "viewBox": f"0 0 {width:g} {height:g}",
    })

    y = 0.0
    for index, ((page, links), (page_width, page_height)) in enumerate(
        zip(page_data, sizes),
        start=1,
    ):
        _prefix_svg_ids(page, f"p{index}-")
        _add_links(page, links)
        page.set("id", f"page-{index}")
        page.set("x", "0")
        page.set("y", f"{y:g}")
        page.set("width", f"{page_width:g}")
        page.set("height", f"{page_height:g}")
        root.append(page)
        y += page_height

    return ETree.ElementTree(root)


def _resolve_svg_path(stem: str, output: Path) -> Path:
    """Map an output argument to the SVG file path, creating directories.

    ``output`` is treated as a file if it ends in ``.svg`` and as a
    directory otherwise (the SVG is then named ``<stem>.svg``).
    """
    if output.suffix.lower() == ".svg":
        output.parent.mkdir(parents=True, exist_ok=True)
        return output
    output.mkdir(parents=True, exist_ok=True)
    return output / f"{stem}.svg"


def _pdf_to_svg_tree(pdf: Path) -> ETree.ElementTree:
    with pymupdf.open(pdf) as document:
        pages = list(document)
        return _combine_page_svgs(
            PageSvg(
                page.get_svg_image(text_as_path=False),
                _extract_links(page, len(pages)),
            )
            for page in pages
        )


def _write_svg(tree: ETree.ElementTree, svg: Path) -> None:
    tree.write(svg, encoding="unicode", xml_declaration=True)


def mupdf_convert(pdf: PathLike, output: PathLike) -> list[Path]:
    """Convert a PDF to one combined SVG (fonts are not embedded)."""
    pdf = Path(pdf)
    svg = _resolve_svg_path(pdf.stem, Path(output))
    _write_svg(_pdf_to_svg_tree(pdf), svg)
    return [svg]


def typ2svg(
    src: PathLike,
    dst: PathLike | None = None,
    *,
    root: PathLike | None = None,
    font_paths: Iterable[PathLike] | None = None,
    strict_fonts: bool = False,
    keep_pdf: bool = False,
) -> Path | Typ2SvgResult:
    src = Path(src)
    if dst is None:
        dst = src.with_suffix(".svg")
    dst = Path(dst)

    font_paths = list(font_paths or [])
    svg = _resolve_svg_path(src.stem, dst)

    with tempfile.TemporaryDirectory(prefix="typ2svg-") as tmpdir:
        pdf = Path(tmpdir) / f"{src.stem}.pdf"
        compile_typst(src, pdf, root=root, font_paths=font_paths)
        tree = _pdf_to_svg_tree(pdf)
        embed_fonts_tree(tree.getroot(), strict=strict_fonts, font_paths=font_paths)
        _write_svg(tree, svg)

        if keep_pdf:
            kept_pdf = svg.with_suffix(".pdf")
            kept_pdf.write_bytes(pdf.read_bytes())
            return Typ2SvgResult(pdf=kept_pdf, svgs=[svg])

    return svg
