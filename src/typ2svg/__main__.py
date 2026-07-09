from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .pipeline import Typ2SvgResult, typ2svg


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m typ2svg",
        description="Convert a Typst source file to selectable-text SVG.",
    )
    parser.add_argument("src", type=Path, help="Typst source file to compile")
    parser.add_argument(
        "dst",
        type=Path,
        nargs="?",
        help="Output SVG path or directory. Defaults to SRC with .svg suffix.",
    )
    parser.add_argument("--root", type=Path, help="Typst project root")
    parser.add_argument(
        "--font-path",
        action="append",
        default=[],
        type=Path,
        dest="font_paths",
        help="Additional Typst font path. May be passed multiple times.",
    )
    parser.add_argument(
        "--strict-fonts",
        action="store_true",
        help="Fail if a referenced SVG font cannot be embedded.",
    )
    parser.add_argument(
        "--keep-pdf",
        action="store_true",
        help="Keep the intermediate PDF next to the SVG output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = typ2svg(
        args.src,
        args.dst,
        root=args.root,
        font_paths=args.font_paths,
        strict_fonts=args.strict_fonts,
        keep_pdf=args.keep_pdf,
    )

    if isinstance(result, Typ2SvgResult):
        print(result.pdf)
        for svg in result.svgs:
            print(svg)
    elif isinstance(result, list):
        for svg in result:
            print(svg)
    else:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
