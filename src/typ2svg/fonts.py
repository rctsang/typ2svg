from __future__ import annotations

import re
import subprocess
import warnings
from base64 import b64encode
from functools import lru_cache
from io import BytesIO
from os import PathLike, fspath
from shutil import which
from typing import Iterable, NamedTuple

from fontTools import subset as ft_subset
from fontTools.ttLib import TTFont


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
    font = TTFont(variant.location)
    try:
        if text is not None:
            options = ft_subset.Options()
            options.layout_features = ["*"]
            options.notdef_outline = True
            subsetter = ft_subset.Subsetter(options)
            subsetter.populate(text=text)
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


@lru_cache(maxsize=None)
def _font_metadata_aliases(location: str) -> frozenset[str]:
    font = TTFont(location, lazy=True)
    try:
        names: set[str] = set()
        for record in font["name"].names:
            if record.nameID not in FONT_ALIAS_NAME_IDS:
                continue
            try:
                name = record.toUnicode().strip()
            except UnicodeDecodeError:
                continue
            if name:
                names.add(name)
        return frozenset(names)
    finally:
        font.close()


def font_aliases(variant: FontVariant) -> set[str]:
    aliases = {variant.family}
    if variant.location == EMBEDDED_LOCATION:
        return aliases
    try:
        aliases.update(_font_metadata_aliases(variant.location))
    except Exception as error:
        warnings.warn(
            f"could not read font names from {variant.location}: {error}",
            stacklevel=2,
        )
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
