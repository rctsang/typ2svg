from __future__ import annotations

import xml.etree.ElementTree as ETree
from os import PathLike
from pathlib import Path
from typing import Iterable

from .fonts import (
    FontDependency,
    FontVariant,
    encode_variant,
    match_font_variant,
    normalize_font_family,
)

SVG_NAMESPACE = "http://www.w3.org/2000/svg"
ETree.register_namespace("", SVG_NAMESPACE)

GENERIC_FAMILIES = {
    "serif",
    "sans-serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_style(style: str | None) -> dict[str, str]:
    if not style:
        return {}
    declarations: dict[str, str] = {}
    for declaration in style.split(";"):
        name, sep, value = declaration.partition(":")
        if sep:
            declarations[name.strip().lower()] = value.strip()
    return declarations


def _split_font_families(value: str | None) -> list[str]:
    if not value:
        return []

    families: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in value:
        if char in {"'", '"'}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
            current.append(char)
            continue
        if char == "," and quote is None:
            family = "".join(current).strip().strip("'\"")
            if family and family.casefold() not in GENERIC_FAMILIES:
                families.append(family)
            current = []
            continue
        current.append(char)

    family = "".join(current).strip().strip("'\"")
    if family and family.casefold() not in GENERIC_FAMILIES:
        families.append(family)
    return families


def _dependency_from_element(element: ETree.Element) -> list[FontDependency]:
    style = _parse_style(element.get("style"))
    family_value = element.get("font-family") or style.get("font-family")
    families = _split_font_families(family_value)
    if not families:
        return []

    font_style = element.get("font-style") or style.get("font-style")
    font_weight = element.get("font-weight") or style.get("font-weight")
    font_stretch = element.get("font-stretch") or style.get("font-stretch")
    return [
        FontDependency(
            family=family,
            style=font_style,
            weight=font_weight,
            stretch=font_stretch,
        )
        for family in families
    ]


def font_usage(root: ETree.Element) -> dict[FontDependency, set[str]]:
    """Map each font dependency in the tree to the characters it renders.

    Dependencies keep document order. Characters are a superset: all text
    under an element that declares a font is attributed to that font.
    """
    usage: dict[FontDependency, set[str]] = {}
    for element in root.iter():
        dependencies = _dependency_from_element(element)
        if not dependencies:
            continue
        chars = set("".join(element.itertext()))
        for dependency in dependencies:
            usage.setdefault(dependency, set()).update(chars)
    return usage


def get_font_dependencies(svgpath: Path) -> list[FontDependency]:
    return list(font_usage(ETree.parse(svgpath).getroot()))


def _font_face(
    dependency: FontDependency,
    variant: FontVariant,
    text: str | None = None,
) -> str:
    declarations = [
        "@font-face {",
        f'  font-family: "{normalize_font_family(dependency.family)}";',
        f"  font-style: {(variant.style or 'Normal').lower()};",
        f"  font-weight: {variant.weight or '400'};",
    ]
    if variant.stretch:
        declarations.append(f"  font-stretch: {variant.stretch};")
    declarations.extend([
        f'  src: url("data:font/woff2;base64,{encode_variant(variant, text)}") format("woff2");',
        "}",
    ])
    return "\n".join(declarations)


def _find_defs(root: ETree.Element) -> ETree.Element:
    for child in root:
        if _local_name(child.tag) == "defs":
            return child

    defs = ETree.Element(f"{{{SVG_NAMESPACE}}}defs")
    root.insert(0, defs)
    return defs


def embed_fonts_tree(
    root: ETree.Element,
    dependencies: Iterable[FontDependency] | None = None,
    *,
    strict: bool = False,
    font_paths: Iterable[str | PathLike[str]] | None = None,
) -> bool:
    """Embed ``@font-face`` rules into an SVG tree in place.

    When ``dependencies`` is omitted they are read from the tree and each
    font is subset to the characters it renders; explicitly passed
    dependencies embed the full font. Returns whether any rule was added.
    """
    font_paths = list(font_paths or [])
    usage: dict[FontDependency, set[str] | None]
    if dependencies is None:
        usage = dict(font_usage(root))
    else:
        usage = {dependency: None for dependency in dependencies}

    # One @font-face per (SVG family name, matched variant); the subset text
    # is the union over every dependency that maps to it.
    faces: dict[tuple[str, FontVariant], tuple[FontDependency, set[str] | None]] = {}
    for dependency, chars in usage.items():
        variant = match_font_variant(dependency, font_paths=font_paths)
        if variant is None:
            if strict:
                raise ValueError(f"could not find font variant for {dependency.family!r}")
            continue
        key = (normalize_font_family(dependency.family), variant)
        if key not in faces:
            faces[key] = (dependency, None if chars is None else set(chars))
        elif chars is not None and faces[key][1] is not None:
            faces[key][1].update(chars)

    rules = [
        _font_face(
            dependency,
            variant,
            None if chars is None else "".join(sorted(chars)),
        )
        for (_, variant), (dependency, chars) in faces.items()
    ]
    if not rules:
        return False

    defs = _find_defs(root)
    style = ETree.Element(f"{{{SVG_NAMESPACE}}}style")
    style.set("type", "text/css")
    style.text = "\n" + "\n\n".join(rules) + "\n"
    defs.insert(0, style)
    return True


def embed_fonts(
    svgpath: Path,
    dependencies: Iterable[FontDependency] | None = None,
    *,
    strict: bool = False,
    font_paths: Iterable[str | PathLike[str]] | None = None,
) -> None:
    """Embed fonts into the SVG file at ``svgpath`` (see ``embed_fonts_tree``)."""
    tree = ETree.parse(svgpath)
    if embed_fonts_tree(
        tree.getroot(),
        dependencies,
        strict=strict,
        font_paths=font_paths,
    ):
        tree.write(svgpath, encoding="unicode", xml_declaration=True)
