from importlib.resources import files
from shutil import which


def _assert_program(name: str) -> None:
    assert which(name) is not None, f"required external program not found on PATH: {name}"


_assert_program("typst")
_assert_program("mutool")

from .pipeline import compile_typst, convert_pdf_to_svg, typ2svg

ASSETS = files("typ2svg").joinpath("assets")

def assets() -> list[str]:
    return [asset.name for asset in ASSETS.iterdir()]


__all__ = ["assets", "compile_typst", "convert_pdf_to_svg", "typ2svg"]
