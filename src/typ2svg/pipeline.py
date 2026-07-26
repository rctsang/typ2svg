from __future__ import annotations

import re
import subprocess
import tempfile
import xml.etree.ElementTree as ETree
from pathlib import Path
from typing import Iterable, NamedTuple, TypeAlias

import pymupdf

from .svg import embed_fonts

PathLike: TypeAlias = str | Path
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
INKSCAPE_NAMESPACE = "http://www.inkscape.org/namespaces/inkscape"
TRANSFORM_RE = re.compile(r"(matrix|translate)\(([^)]*)\)")

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


def _float_attr(element: ETree.Element, name: str) -> float:
    value = element.get(name)
    if value is None:
        raise ValueError(f"SVG page is missing {name!r}")
    for suffix in ("pt", "px"):
        if value.endswith(suffix):
            value = value.removesuffix(suffix)
            break
    return float(value)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parent_map(root: ETree.Element) -> dict[ETree.Element, ETree.Element]:
    return {child: parent for parent in root.iter() for child in parent}


def _rect_tuple(rect) -> tuple[float, float, float, float]:
    return (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))


def _contains_point(rect: tuple[float, float, float, float], x: float, y: float) -> bool:
    return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]


def _linked_text(page, rect: tuple[float, float, float, float]) -> str:
    get_text = getattr(page, "get_text", None)
    if get_text is None:
        return ""

    text: list[str] = []
    for block in get_text("rawdict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    bbox = tuple(
                        float(value) for value in char.get("bbox", (0, 0, 0, 0))
                    )
                    if _contains_point(
                        rect,
                        (bbox[0] + bbox[2]) / 2,
                        (bbox[1] + bbox[3]) / 2,
                    ):
                        text.append(char.get("c", ""))
    return "".join(text).strip()


def _safe_launch_href(link: dict) -> str | None:
    href = link.get("file")
    if not isinstance(href, str):
        return None
    if href.startswith("#"):
        return href
    if href.startswith(("http://", "https://", "mailto:")):
        return href
    return None


def _extract_links(page, page_count: int) -> list[PdfLink]:
    get_links = getattr(page, "get_links", None)
    if get_links is None:
        return []

    links: list[PdfLink] = []
    for link in get_links():
        rect = _rect_tuple(link["from"])
        kind = link.get("kind")
        href: str | None = None
        if kind == pymupdf.LINK_URI:
            href = link.get("uri")
        elif kind == pymupdf.LINK_GOTO:
            target_page = link.get("page")
            if isinstance(target_page, int) and 0 <= target_page < page_count:
                href = f"#page-{target_page + 1}"
        elif kind == pymupdf.LINK_LAUNCH:
            href = _safe_launch_href(link)
        if href:
            links.append(PdfLink(href, rect, _linked_text(page, rect)))
    return links


def _prefix_svg_ids(root: ETree.Element, prefix: str) -> None:
    id_map = {
        value: f"{prefix}{value}"
        for element in root.iter()
        if (value := element.get("id"))
    }
    if not id_map:
        return

    for element in root.iter():
        element_id = element.get("id")
        if element_id in id_map:
            element.set("id", id_map[element_id])
        for name, value in list(element.attrib.items()):
            if name == "id":
                continue
            updated = value
            for old_id, new_id in id_map.items():
                updated = updated.replace(f"url(#{old_id})", f"url(#{new_id})")
                updated = updated.replace(f"url('#{old_id}')", f"url('#{new_id}')")
                updated = updated.replace(f'url(\"#{old_id}\")', f'url(\"#{new_id}\")')
                if updated == f"#{old_id}":
                    updated = f"#{new_id}"
            if updated != value:
                element.set(name, updated)


def _split_coordinates(value: str | None) -> list[str] | None:
    if value is None:
        return None
    coordinates = value.replace(",", " ").split()
    return coordinates or None


def _parse_transform_numbers(value: str) -> list[float]:
    return [float(number) for number in re.split(r"[\s,]+", value.strip()) if number]


def _multiply_matrix(
    first: tuple[float, float, float, float, float, float],
    second: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
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


def _parse_transform(value: str | None) -> tuple[float, float, float, float, float, float]:
    matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    if not value:
        return matrix

    for name, arguments in TRANSFORM_RE.findall(value):
        numbers = _parse_transform_numbers(arguments)
        if name == "matrix" and len(numbers) == 6:
            transform = tuple(numbers)
        elif name == "translate" and numbers:
            transform = (
                1.0,
                0.0,
                0.0,
                1.0,
                numbers[0],
                numbers[1] if len(numbers) > 1 else 0.0,
            )
        else:
            continue
        matrix = _multiply_matrix(matrix, transform)
    return matrix


def _element_transform(
    element: ETree.Element,
    parents: dict[ETree.Element, ETree.Element],
) -> tuple[float, float, float, float, float, float]:
    chain = [element]
    while chain[-1] in parents:
        chain.append(parents[chain[-1]])

    matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for item in reversed(chain):
        matrix = _multiply_matrix(matrix, _parse_transform(item.get("transform")))
    return matrix


def _transform_point(
    matrix: tuple[float, float, float, float, float, float],
    x: float,
    y: float,
) -> tuple[float, float]:
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
) -> ETree.Element:
    copied = ETree.Element(element.tag, element.attrib)
    copied.text = text
    if x is not None:
        copied.set("x", " ".join(x))
    else:
        copied.attrib.pop("x", None)
    return copied


def _link_element(href: str) -> ETree.Element:
    link = ETree.Element(f"{{{SVG_NAMESPACE}}}a")
    link.set("href", href)
    link.set(f"{{{XLINK_NAMESPACE}}}href", href)
    return link


def _wrap_text_element(
    root: ETree.Element,
    element: ETree.Element,
    start: int,
    end: int,
    href: str,
) -> None:
    parent = _parent_map(root)[element]
    child_index = list(parent).index(element)
    text = element.text or ""
    coordinates = _split_coordinates(element.get("x"))
    split_x = (
        coordinates
        if coordinates is not None and len(coordinates) >= len(text)
        else None
    )
    replacements: list[ETree.Element] = []

    if start:
        replacements.append(
            _copy_text_element(element, text[:start], split_x[:start] if split_x else None)
        )

    link = _link_element(href)
    linked = _copy_text_element(
        element,
        text[start:end],
        split_x[start:end] if split_x else None,
    )
    link.append(linked)
    replacements.append(link)

    if end < len(text):
        replacements.append(
            _copy_text_element(element, text[end:], split_x[end:] if split_x else None)
        )

    replacements[-1].tail = element.tail
    parent.remove(element)
    for offset, replacement in enumerate(replacements):
        parent.insert(child_index + offset, replacement)


def _text_center_score(
    parents: dict[ETree.Element, ETree.Element],
    element: ETree.Element,
    start: int,
    end: int,
    rect: tuple[float, float, float, float],
) -> float:
    coordinates = [
        float(value) for value in _split_coordinates(element.get("x")) or []
    ]
    y = element.get("y")
    if y is None or start >= len(coordinates):
        return 1_000_000_000.0
    x0 = float(coordinates[start])
    x1 = _range_end_x(coordinates, end)
    cx, cy = _transform_point(
        _element_transform(element, parents),
        (x0 + x1) / 2,
        float(y),
    )
    rx = (rect[0] + rect[2]) / 2
    ry = (rect[1] + rect[3]) / 2
    penalty = 0.0 if _contains_point(rect, cx, cy) else 1_000_000.0
    return penalty + abs(cx - rx) + abs(cy - ry)


def _wrap_link_text(root: ETree.Element, link: PdfLink) -> bool:
    if not link.text:
        return False

    candidates: list[tuple[float, ETree.Element, int, int]] = []
    parents = _parent_map(root)
    for element in root.iter():
        if _local_name(element.tag) != "tspan" or not element.text:
            continue
        start = element.text.find(link.text)
        while start >= 0:
            end = start + len(link.text)
            candidates.append((
                _text_center_score(parents, element, start, end, link.rect),
                element,
                start,
                end,
            ))
            start = element.text.find(link.text, start + 1)

    if not candidates:
        return False

    _, element, start, end = min(candidates, key=lambda candidate: candidate[0])
    _wrap_text_element(root, element, start, end, link.href)
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
    for link in links:
        if not _wrap_link_text(root, link):
            _add_link_rect(root, link)


def _combine_page_svgs(page_svgs: Iterable[PageSvg]) -> ETree.ElementTree:
    page_data = [(ETree.fromstring(page.svg), page.links) for page in page_svgs]
    pages = [page for page, _ in page_data]
    if not pages:
        raise ValueError("PDF has no pages")

    sizes = [(_float_attr(page, "width"), _float_attr(page, "height")) for page in pages]
    width = max(page_width for page_width, _ in sizes)
    height = sum(page_height for _, page_height in sizes)
    root = ETree.Element(f"{{{SVG_NAMESPACE}}}svg", {
        "version": "1.1",
        "width": f"{width:g}",
        "height": f"{height:g}",
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


def mupdf_convert(pdf: PathLike, output: PathLike) -> list[Path]:
    pdf = Path(pdf)
    output = Path(output)
    if output.suffix.lower() == ".svg":
        output.parent.mkdir(parents=True, exist_ok=True)
        svg = output
    else:
        output.mkdir(parents=True, exist_ok=True)
        svg = output / f"{pdf.stem}.svg"

    with pymupdf.open(pdf) as document:
        pages = list(document)
        tree = _combine_page_svgs(
            PageSvg(
                page.get_svg_image(text_as_path=False),
                _extract_links(page, len(pages)),
            )
            for page in pages
        )
    tree.write(svg, encoding="unicode", xml_declaration=True)
    return [svg]


def typ2svg(
    src: PathLike,
    dst: PathLike | None = None,
    *,
    root: PathLike | None = None,
    font_paths: Iterable[PathLike] | None = None,
    strict_fonts: bool = False,
    keep_pdf: bool = False,
) -> Path | list[Path] | Typ2SvgResult:
    src = Path(src)
    if dst is None:
        dst = src.with_suffix(".svg")
    dst = Path(dst)

    with tempfile.TemporaryDirectory(prefix="typ2svg-") as tmpdir:
        pdf = Path(tmpdir) / f"{src.stem}.pdf"
        compile_typst(src, pdf, root=root, font_paths=font_paths)
        svgs = mupdf_convert(pdf, dst)
        for svg in svgs:
            embed_fonts(svg, strict=strict_fonts)

        if keep_pdf:
            kept_pdf = dst.with_suffix(".pdf") if dst.suffix else dst / f"{src.stem}.pdf"
            kept_pdf.parent.mkdir(parents=True, exist_ok=True)
            kept_pdf.write_bytes(pdf.read_bytes())
            return Typ2SvgResult(pdf=kept_pdf, svgs=svgs)

    return svgs[0] if len(svgs) == 1 else svgs
