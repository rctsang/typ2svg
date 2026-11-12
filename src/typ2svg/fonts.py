from __future__ import annotations

import logging
import re
import subprocess
from base64 import b64encode
from functools import lru_cache
from io import BytesIO
from os import PathLike, fspath
from shutil import which
from typing import Iterable, NamedTuple

from fontTools import subset as ft_subset
from fontTools.ttLib import TTCollection, TTFont

log = logging.getLogger(__name__)


class FontVariant(NamedTuple):
    family: str
    location: str
    style: str | None
    weight: str | None
    stretch: str | None
    variable: bool


class FontDependency(NamedTuple):
    family: str
    style: str | None = None
    weight: str | None = None
    stretch: str | None = None


RE_VARIANT = re.compile(
    r"^\s*[├└]\s+(?P<location>.+?)(?P<variable> \(Variable\))?$")
RE_ATTR = re.compile(
    r"(Style|Weight|Stretch):\s*([^,]+)")
FONT_ALIAS_NAME_IDS = {1, 4, 6, 16, 21}
EMBEDDED_LOCATION = "(Embedded)"
FAMILY_NAME_IDS = {1, 16, 21}


def require_program(name: str) -> None:
    """Raise a clear error if an external program is not on PATH."""
    if which(name) is None:
        raise FileNotFoundError(f"required external program not found on PATH: {name}")


def _font_paths_key(font_paths: Iterable[str | PathLike[str]] | None) -> tuple[str, ...]:
    return tuple(fspath(path) for path in font_paths or ())


def get_fonts(font_paths: Iterable[str | PathLike[str]] | None = None) -> list[FontVariant]:
    """Return the font variants typst can see, including ``font_paths``."""
    return list(_get_fonts(_font_paths_key(font_paths)))


@lru_cache(maxsize=None)
def _get_fonts(font_paths: tuple[str, ...]) -> tuple[FontVariant, ...]:
    require_program("typst")
    command = ["typst", "fonts", "--variants"]
    for font_path in font_paths:
        command.extend(["--font-path", font_path])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return tuple(parse_font_variants(result.stdout))


def parse_font_variants(output: str) -> list[FontVariant]:
    """Parse the output of ``typst fonts --variants``."""
    variants: list[FontVariant] = []
    family: str | None = None
    current: dict[str, str | bool | None] | None = None

    def finish_current() -> None:
        if family is None or current is None:
            return
        variants.append(FontVariant(
            family=family,
            location=str(current['location']),
            style=current.get('style'),
            weight=current.get('weight'),
            stretch=current.get('stretch'),
            variable=bool(current['variable']),
        ))

    for line in output.splitlines():
        if not line.strip():
            finish_current()
            current = None
            continue

        if not line.startswith(" "):
            finish_current()
            current = None
            family = line.strip()
            continue

        if (m := RE_VARIANT.match(line)):
            finish_current()
            current = {
                'location': m.group('location'),
                'style': None,
                'weight': None,
                'stretch': None,
                'variable': m.group('variable') is not None,
            }
            continue

        if current is not None:
            for name, value in RE_ATTR.findall(line):
                current[name.lower()] = value.strip()

    finish_current()
    return variants


def encode_variant(variant: FontVariant, text: str | None = None) -> str:
    """Return base64-encoded woff2 data for the font variant.

    If ``text`` is given, the font is subset to the glyphs needed to render it.
    """
    if variant.location == EMBEDDED_LOCATION:
        raise ValueError(
            f"font variant must be installed locally: {variant.family}"
        )
    font = TTFont(variant.location, fontNumber=_font_number(variant))
    try:
        if text is not None:
            options = ft_subset.Options()
            options.layout_features = ["*"]
            options.notdef_outline = True
            subsetter = ft_subset.Subsetter(options)
            subsetter.populate(text=text)
            # The subsetter logs notes (e.g. "FFTM NOT subset; dropped") for
            # tables it drops; they are harmless, so keep them out of stderr.
            with _quiet_logger("fontTools.subset"):
                subsetter.subset(font)
        font.flavor = "woff2"
        buf = BytesIO()
        font.save(buf)
    finally:
        font.close()
    return b64encode(buf.getvalue()).decode('ascii')


def normalize_font_family(family: str) -> str:
    return family.strip().strip("'\"")


def _font_alias_key(family: str) -> str:
    family = normalize_font_family(family)
    if len(family) > 7 and family[6] == "+" and family[:6].isupper():
        family = family[7:]
    return family.casefold()


class _Face(NamedTuple):
    """Metadata for one font inside a font file (or font collection)."""
    aliases: frozenset[str]
    families: frozenset[str]
    weight: int | None
    italic: bool


class _quiet_logger:
    def __init__(self, name: str, level: int = logging.ERROR) -> None:
        self.logger = logging.getLogger(name)
        self.level = level

    def __enter__(self) -> None:
        self.previous = self.logger.level
        self.logger.setLevel(self.level)

    def __exit__(self, *args: object) -> None:
        self.logger.setLevel(self.previous)


def _face_metadata(font: TTFont) -> _Face:
    aliases: set[str] = set()
    families: set[str] = set()
    for record in font["name"].names:
        if record.nameID not in FONT_ALIAS_NAME_IDS:
            continue
        try:
            name = record.toUnicode().strip()
        except UnicodeDecodeError:
            continue
        if not name:
            continue
        aliases.add(name)
        if record.nameID in FAMILY_NAME_IDS:
            families.add(name.casefold())

    weight: int | None = None
    italic = False
    if "OS/2" in font:
        os2 = font["OS/2"]
        weight = int(os2.usWeightClass)
        italic = bool(os2.fsSelection & 0b1) or bool(os2.fsSelection & 0b1000000000)
    elif "head" in font:
        italic = bool(font["head"].macStyle & 0b10)
    return _Face(frozenset(aliases), frozenset(families), weight, italic)


def _is_collection(location: str) -> bool:
    with open(location, "rb") as file:
        return file.read(4) == b"ttcf"


@lru_cache(maxsize=None)
def _font_faces(location: str) -> tuple[_Face, ...]:
    """Read every face in a font file; ``.ttc``/``.otc`` files hold several."""
    if _is_collection(location):
        collection = TTCollection(location, lazy=True)
        try:
            return tuple(_face_metadata(font) for font in collection.fonts)
        finally:
            collection.close()
    font = TTFont(location, lazy=True)
    try:
        return (_face_metadata(font),)
    finally:
        font.close()


def _face_index(variant: FontVariant) -> int | None:
    """Index of the face in ``variant.location`` that best matches ``variant``.

    Typst lists collection faces by file path only, so the face is picked by
    family name, then weight and italic flag.
    """
    try:
        faces = _font_faces(variant.location)
    except Exception as error:
        # Non-critical: the font just can't be matched by alias or embedded.
        log.debug("could not read font %s: %s", variant.location, error)
        return None
    if len(faces) == 1:
        return 0

    family = variant.family.casefold()
    weight = _normalize_weight(variant.weight)
    italic = _normalize_style(variant.style) in {"Italic", "Oblique"}

    def score(index: int) -> tuple[int, int, int]:
        face = faces[index]
        weight_score = 0
        if weight is not None and weight.isdigit() and face.weight is not None:
            weight_score = -abs(int(weight) - face.weight)
        return (family in face.families, face.italic == italic, weight_score)

    return max(range(len(faces)), key=score)


def _font_number(variant: FontVariant) -> int:
    index = _face_index(variant)
    return 0 if index is None else index


def font_aliases(variant: FontVariant) -> set[str]:
    aliases = {variant.family}
    if variant.location == EMBEDDED_LOCATION:
        return aliases
    index = _face_index(variant)
    if index is not None:
        aliases.update(_font_faces(variant.location)[index].aliases)
    return aliases


def _normalize_style(style: str | None) -> str | None:
    if style is None:
        return None
    return style.strip().title()


def _normalize_weight(weight: str | None) -> str | None:
    if weight is None:
        return None
    weight = weight.strip().lower()
    aliases = {
        "normal": "400",
        "regular": "400",
        "bold": "700",
    }
    return aliases.get(weight, weight)


def _normalize_stretch(stretch: str | None) -> str | None:
    if stretch is None:
        return None
    return stretch.strip()


def match_font_variant(
    dependency: FontDependency,
    variants: list[FontVariant] | None = None,
    *,
    font_paths: Iterable[str | PathLike[str]] | None = None,
) -> FontVariant | None:
    """Return the best locally available Typst font variant for a dependency."""

    variants = variants if variants is not None else get_fonts(font_paths)
    family_key = _font_alias_key(dependency.family)
    style = _normalize_style(dependency.style)
    weight = _normalize_weight(dependency.weight)
    stretch = _normalize_stretch(dependency.stretch)

    candidates = [
        variant for variant in variants
        if variant.location != EMBEDDED_LOCATION
        and _font_alias_key(variant.family) == family_key
    ]
    if not candidates:
        candidates = [
            variant for variant in variants
            if variant.location != EMBEDDED_LOCATION
            and family_key in {_font_alias_key(alias) for alias in font_aliases(variant)}
        ]
    if not candidates:
        return None

    slanted = {"Italic", "Oblique"}

    def score(variant: FontVariant) -> int:
        value = 0
        variant_style = _normalize_style(variant.style)
        if style is not None:
            if variant_style == style:
                value += 4
            elif style in slanted and variant_style in slanted:
                value += 3
        if weight is not None and _normalize_weight(variant.weight) == weight:
            value += 4
        if stretch is not None and variant.stretch == stretch:
            value += 2
        if variant_style == "Normal":
            value += 1
        if _normalize_weight(variant.weight) == "400":
            value += 1
        return value

    return max(candidates, key=score)
