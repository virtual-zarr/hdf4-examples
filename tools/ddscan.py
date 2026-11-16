"""Summarise an HDF4 file's data descriptors straight from its bytes.

This is the one part of the answer key that does not come from the HDF4 C
library. It reads only the descriptor list and the 2-byte kind of each special
element, which is enough to say which object types and storage schemes a file
contains. It is used for tagging, never as a reference for values or offsets.
"""

from __future__ import annotations

import struct
from typing import Any

MAGIC = b"\x0e\x03\x13\x01"
SPECIAL_BIT = 0x4000

# hdf/src/htags.h
TAGS = {
    1: "NULL", 20: "LINKED", 30: "VERSION", 40: "COMPRESSED", 50: "VLINKED",
    51: "VLINKED_DATA", 60: "CHUNKED", 61: "CHUNK", 100: "FID", 101: "FD",
    102: "TID", 103: "TD", 104: "DIL", 105: "DIA", 106: "NT", 107: "MT",
    108: "FREE", 200: "ID8", 201: "IP8", 202: "RI8", 203: "CI8", 204: "II8",
    300: "ID", 301: "LUT", 302: "RI", 303: "CI", 304: "NRI", 306: "RIG",
    307: "LD", 308: "MD", 309: "MA", 310: "CCN", 311: "CFM", 312: "AR",
    400: "DRAW", 401: "RUN", 500: "XYP", 501: "MTO", 602: "T14", 603: "T105",
    700: "SDG", 701: "SDD", 702: "SD", 703: "SDS", 704: "SDL", 705: "SDU",
    706: "SDF", 707: "SDM", 708: "SDC", 709: "SDT", 710: "SDLNK", 720: "NDG",
    731: "CAL", 732: "FV", 1962: "VH", 1963: "VS", 1965: "VG",
}
# hdf/src/hfile.h SPECIAL_*
SPECIAL = {1: "linked", 2: "external", 3: "compressed", 4: "vlinked", 5: "chunked",
           6: "buffered", 7: "compressed_raster"}


def descriptors(raw: bytes) -> list[tuple[int, bool, int, int, int]]:
    """Return (tag, special, ref, offset, length) for every non-empty descriptor."""
    if raw[:4] != MAGIC:
        raise ValueError("not an HDF4 file: wrong magic number")
    out, position, seen = [], 4, set()
    while True:
        if position in seen or position + 6 > len(raw):
            raise ValueError(f"descriptor block chain is broken at offset {position}")
        seen.add(position)
        count, following = struct.unpack_from(">HI", raw, position)
        position += 6
        if position + 12 * count > len(raw):
            raise ValueError("descriptor block runs past the end of the file")
        for _ in range(count):
            tag, ref, offset, length = struct.unpack_from(">HHII", raw, position)
            position += 12
            if tag != 1:
                out.append((tag & ~SPECIAL_BIT, bool(tag & SPECIAL_BIT), ref, offset, length))
        if following == 0:
            return out
        position = following


def summary(raw: bytes) -> dict[str, Any]:
    tags: dict[str, int] = {}
    special: dict[str, dict[str, int]] = {}
    for tag, is_special, _ref, offset, _length in descriptors(raw):
        name = TAGS.get(tag, str(tag))
        tags[name] = tags.get(name, 0) + 1
        if is_special and offset + 2 <= len(raw):
            kind = SPECIAL.get(struct.unpack_from(">H", raw, offset)[0], "unknown")
            special.setdefault(name, {})
            special[name][kind] = special[name].get(kind, 0) + 1
    return {
        "derived_by": "tools/ddscan.py from the file's descriptor list",
        "tags": dict(sorted(tags.items())),
        "special_elements": {k: dict(sorted(v.items())) for k, v in sorted(special.items())},
    }
