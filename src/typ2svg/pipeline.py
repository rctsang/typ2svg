from __future__ import annotations

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


def _combine_page_svgs(page_svgs: Iterable[str]) -> ETree.ElementTree:
    pages = [ETree.fromstring(svg) for svg in page_svgs]
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
    for index, (page, (page_width, page_height)) in enumerate(zip(pages, sizes), start=1):
        _prefix_svg_ids(page, f"p{index}-")
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
        tree = _combine_page_svgs(
            page.get_svg_image(text_as_path=False)
            for page in document
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
