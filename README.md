# typ2svg

a simple package that pipelines the conversion of a compiled typst pdf to text-based svg.

## why?

typst can compile to svg, but the resulting svg is entirely glyph-based to preserve exact visual layout.

for various reasons, i want an svg output that has selectable text, and determined that the following pipeline is somewhat viable:
- typst compile to pdf: `typst compile ...`
- PyMuPDF render pdf to text-based svg
- post-process the svg by embedding the required font in base64 via css style tag
- optionally use inkscape to handle additional formatting

## usage

```python
from typ2svg import typ2svg

typ2svg("input.typ", "output.svg")
```

or from the command line:

```bash
typ2svg input.typ output.svg
```

`typ2svg` asserts at import time that `typst` is available on `PATH`.
