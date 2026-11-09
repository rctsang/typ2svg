from importlib.resources import files

from .pipeline import compile_typst, mupdf_convert, typ2svg

ASSETS = files("typ2svg").joinpath("assets")

def assets() -> list[str]:
    return [asset.name for asset in ASSETS.iterdir()]


__all__ = ["assets", "compile_typst", "mupdf_convert", "typ2svg"]
