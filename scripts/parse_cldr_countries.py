#!/usr/bin/env python3
"""Parse CLDR's English locale data into the app's country format."""

from pathlib import Path
from unicodedata import combining as cmb
from unicodedata import normalize
from xml.etree.ElementTree import parse


def main() -> None:
    root = Path(__file__).parent.parent
    source = root / "vendor" / "unicode-org" / "en.xml"
    output = root / "bokehbowl" / "resources" / "countries.txt"
    excluded = {"EU", "EZ", "QO", "UN", "XA", "XB", "ZZ"}
    countries = {
        node.get("type"): node.text
        for node in parse(source).findall("./localeDisplayNames/territories/territory")
        if len(node.get("type")) == 2
        and node.get("type") not in excluded
        and node.get("alt") is None
    }
    names = sorted(
        countries.values(),
        key=lambda s: "".join(c for c in normalize("NFKD", s) if not cmb(c)).casefold(),
    )
    output.write_text("".join(f"{name}\n" for name in names), encoding="utf-8")


if __name__ == "__main__":
    main()
