from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, NamedTuple, TypeAlias

from .svg import embed_fonts

PathLike: TypeAlias = str | Path


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


def convert_pdf_to_svg(pdf: PathLike, output: PathLike) -> list[Path]:
    pdf = Path(pdf)
    output = Path(output)
    if output.suffix.lower() == ".svg":
        output.parent.mkdir(parents=True, exist_ok=True)
        pattern = output
    else:
        output.mkdir(parents=True, exist_ok=True)
        pattern = output / f"{pdf.stem}-%d.svg"

    command = [
        "mutool",
        "convert",
        "-F",
        "svg",
        "-O",
        "text=text",
        "-o",
        str(pattern),
        str(pdf),
    ]
    _run(command)

    if pattern.exists():
        return [pattern]
    return sorted(pattern.parent.glob(pattern.name.replace("%d", "*")))


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
        svgs = convert_pdf_to_svg(pdf, dst)
        for svg in svgs:
            embed_fonts(svg, strict=strict_fonts)

        if keep_pdf:
            kept_pdf = dst.with_suffix(".pdf") if dst.suffix else dst / f"{src.stem}.pdf"
            kept_pdf.parent.mkdir(parents=True, exist_ok=True)
            kept_pdf.write_bytes(pdf.read_bytes())
            return Typ2SvgResult(pdf=kept_pdf, svgs=svgs)

    return svgs[0] if len(svgs) == 1 else svgs
