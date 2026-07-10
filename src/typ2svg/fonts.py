from __future__ import annotations

import re
import subprocess
from base64 import b64encode
from io import BytesIO
from typing import NamedTuple

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

_VARIANTS: list[FontVariant]|None = None

def get_fonts() -> list[FontVariant]:
    """returns the list of available font variants from typst"""

    # memoized
    global _VARIANTS
    if _VARIANTS is not None:
        return _VARIANTS

    result = subprocess.run(
        ['typst', 'fonts', '--variants'],
        check=True,
        capture_output=True,
        text=True,
    )

    variants: list[FontVariant] = []
    family: str|None = None
    current: dict[str, str|bool|None] | None = None

    def finish_current() -> None:
        if family is None or current is None:
            return

        variant = FontVariant(
            family=family,
            location=str(current['location']),
            style=current.get('style'),
            weight=current.get('weight'),
            stretch=current.get('stretch'),
            variable=bool(current['variable']),
        )
        variants.append(variant)

    for line in result.stdout.splitlines():
        if not line.strip():
            current = finish_current()
            continue

        if not line.startswith(" "):
            current = finish_current()
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
    _VARIANTS = variants
    return variants

def encode_variant(variant: FontVariant) -> str:
    """get the base64-encoded woff2 data of the font variant ttf"""
    assert variant.location != "(Embedded)", \
        "font variant must be installed locally: {}".format(variant.family)
    font = TTFont(variant.location)
    font.flavor = "woff2"
    buf = BytesIO()
    font.save(buf)
    return b64encode(buf.getvalue()).decode('ascii')


def normalize_font_family(family: str) -> str:
    return family.strip().strip("'\"")


def _font_family_key(family: str) -> str:
    return "".join(
        char for char in normalize_font_family(family).casefold()
        if char.isalnum()
    )


def _normalize_style(style: str | None) -> str | None:
    if style is None:
        return None
    style = style.strip().lower()
    if style == "normal":
        return "Normal"
    if style in {"italic", "oblique"}:
        return "Italic"
    return style.title()


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
) -> FontVariant | None:
    """Return the best locally available Typst font variant for a dependency."""

    variants = variants if variants is not None else get_fonts()
    family = normalize_font_family(dependency.family).casefold()
    family_key = _font_family_key(dependency.family)
    style = _normalize_style(dependency.style)
    weight = _normalize_weight(dependency.weight)
    stretch = _normalize_stretch(dependency.stretch)

    candidates = [
        variant for variant in variants
        if variant.location != "(Embedded)"
        and (
            variant.family.casefold() == family
            or _font_family_key(variant.family) == family_key
        )
    ]
    if not candidates:
        return None

    def score(variant: FontVariant) -> int:
        value = 0
        if style is not None and variant.style == style:
            value += 4
        if weight is not None and _normalize_weight(variant.weight) == weight:
            value += 4
        if stretch is not None and variant.stretch == stretch:
            value += 2
        if variant.style == "Normal":
            value += 1
        if _normalize_weight(variant.weight) == "400":
            value += 1
        return value

    return max(candidates, key=score)
