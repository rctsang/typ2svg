from __future__ import annotations

import xml.etree.ElementTree as ETree
from pathlib import Path
from typing import Iterable

from .fonts import FontDependency, encode_variant, match_font_variant

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


def get_font_dependencies(svgpath: Path) -> list[FontDependency]:
    tree = ETree.parse(svgpath)
    dependencies: list[FontDependency] = []
    seen: set[FontDependency] = set()

    for element in tree.iter():
        for dependency in _dependency_from_element(element):
            if dependency not in seen:
                dependencies.append(dependency)
                seen.add(dependency)

    return dependencies


def _font_face(dependency: FontDependency) -> str | None:
    variant = match_font_variant(dependency)
    if variant is None:
        return None

    declarations = [
        "@font-face {",
        f'  font-family: "{variant.family}";',
        f"  font-style: {(variant.style or 'Normal').lower()};",
        f"  font-weight: {variant.weight or '400'};",
    ]
    if variant.stretch:
        declarations.append(f"  font-stretch: {variant.stretch};")
    declarations.extend([
        f'  src: url("data:font/woff2;base64,{encode_variant(variant)}") format("woff2");',
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


def embed_fonts(
    svgpath: Path,
    dependencies: Iterable[FontDependency] | None = None,
    *,
    strict: bool = False,
) -> None:
    dependencies = list(dependencies) if dependencies is not None else get_font_dependencies(svgpath)
    rules: list[str] = []
    seen_variants: set[tuple[str, str | None, str | None, str | None]] = set()

    for dependency in dependencies:
        variant = match_font_variant(dependency)
        if variant is None:
            if strict:
                raise ValueError(f"could not find font variant for {dependency.family!r}")
            continue
        key = (variant.family, variant.style, variant.weight, variant.stretch)
        if key in seen_variants:
            continue
        seen_variants.add(key)
        rule = _font_face(dependency)
        if rule is not None:
            rules.append(rule)

    if not rules:
        return

    tree = ETree.parse(svgpath)
    root = tree.getroot()
    defs = _find_defs(root)
    style = ETree.Element(f"{{{SVG_NAMESPACE}}}style")
    style.set("type", "text/css")
    style.text = "\n" + "\n\n".join(rules) + "\n"
    defs.insert(0, style)
    tree.write(svgpath, encoding="unicode", xml_declaration=True)
