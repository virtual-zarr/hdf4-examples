"""Describe an HDF4 file as the HDF4 C library sees it.

Writes the ``.expected.json`` answer key: for every scientific dataset its
type, shape, dimensions, attributes, fill value, a hash of its decoded values
and the byte ranges that hold its stored bytes; for every user Vdata its
fields and records; the Vgroup tree; and HDF-EOS2 structure metadata.

Everything under ``"sds"``, ``"vdata"`` and ``"vgroups"`` comes from the C
library. ``"descriptors"`` and ``"eos"`` are derived by this tool from the raw
bytes and from the StructMetadata text, and are labelled as such.

    uv run tools/describe.py data/synthetic/contiguous-int16/contiguous-int16.hdf
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import itertools
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyhdf.V  # noqa: F401  (adds vgstart to HDF)
import pyhdf.VS  # noqa: F401  (adds vstart to HDF)
from pyhdf.error import HDF4Error
from pyhdf.HDF import HC, HDF
from pyhdf.SD import SD

import ddscan
import hdf4lib

FORMAT = "hdf4-expected/1"

# Arrays and tables at or under this many elements are stored inline as well as hashed.
INLINE_LIMIT = 64

# hdf/src/hntdefs.h
NUMBER_TYPES = {
    3: ("uchar8", "u1"),
    4: ("char8", "S1"),
    5: ("float32", "f4"),
    6: ("float64", "f8"),
    20: ("int8", "i1"),
    21: ("uint8", "u1"),
    22: ("int16", "i2"),
    23: ("uint16", "u2"),
    24: ("int32", "i4"),
    25: ("uint32", "u4"),
    26: ("int64", "i8"),
    27: ("uint64", "u8"),
}
# mfhdf/libsrc/hcomp.h comp_coder_t
CODERS = {0: "none", 1: "rle", 2: "nbit", 3: "skphuff", 4: "deflate", 5: "szip"}
# Vdata classes the library creates for its own bookkeeping.
INTERNAL_VDATA_CLASSES = (
    "Attr0.0",
    "DimVal0.0",
    "DimVal0.1",
    "_HDF_CHK_TBL_",
    "SDSVar",
    "CoordVar",
    "RIATTR0.0",
)
INTERNAL_VGROUP_CLASSES = ("CDF0.0", "Var0.0", "Dim0.0", "UDim0.0", "RIG0.0", "RI0.0")


def number_type(code: int) -> tuple[str, str, str]:
    """Return (HDF4 type name, numpy dtype without byte order, stored byte order)."""
    little = bool(code & hdf4lib.DFNT_LITEND)
    base = code & ~hdf4lib.DFNT_LITEND
    if base not in NUMBER_TYPES:
        raise ValueError(f"number type {code} is not handled by this tool")
    name, dtype = NUMBER_TYPES[base]
    order = "none" if np.dtype(dtype).itemsize == 1 else ("little" if little else "big")
    return name, dtype, order


def jsonable(value: Any) -> Any:
    """Convert numpy and bytes values to JSON, keeping non-finite floats as strings."""
    if isinstance(value, np.ndarray):
        return jsonable(value.tolist())
    if isinstance(value, np.generic):
        return jsonable(value.item())
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return {"bytes": list(value)}
    if isinstance(value, float) and not math.isfinite(value):
        return {"float": repr(value)}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def canonical_bytes(array: np.ndarray) -> bytes:
    """The bytes that ``values_sha256`` hashes: C order, little-endian, no padding."""
    if array.dtype.kind in "SU":
        return np.ascontiguousarray(array.astype("S1")).tobytes()
    return np.ascontiguousarray(array.astype(array.dtype.newbyteorder("<"))).tobytes()


def attribute(value: Any, type_code: int, length: int) -> dict[str, Any]:
    name, _, _ = number_type(type_code)
    return {"type": name, "length": length, "value": jsonable(value)}


def attributes(owner) -> dict[str, Any]:
    out = {}
    for name, (value, _index, type_code, length) in owner.attributes(full=1).items():
        out[name] = attribute(value, type_code, length)
    return out


def read_values(sds, shape: list[int], type_code: int) -> np.ndarray:
    """Read a whole dataset, through pyhdf where it can and SDreaddata where it cannot."""
    _, dtype, _ = number_type(type_code)
    if any(n == 0 for n in shape):
        return np.zeros(shape, dtype)
    try:
        return np.asarray(sds.get())
    except HDF4Error:
        out = np.zeros(shape, dtype)
        rank = len(shape)
        start = (hdf4lib.I32 * rank)(*([0] * rank))
        edge = (hdf4lib.I32 * rank)(*shape)
        hdf4lib.mfhdf.SDreaddata.argtypes = [
            hdf4lib.I32,
            ctypes.POINTER(hdf4lib.I32),
            ctypes.POINTER(hdf4lib.I32),
            ctypes.POINTER(hdf4lib.I32),
            ctypes.c_void_p,
        ]
        if hdf4lib.mfhdf.SDreaddata(sds._id, start, None, edge, out.ctypes.data) != 0:
            raise RuntimeError("SDreaddata failed") from None
        return out


def merge_adjacent(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Join ranges that follow each other with no gap, keeping their order.

    The library reports linked-block data one block at a time, and blocks that
    were allocated back to back are a single run of bytes in the file. Merging
    them gives one normal form that any reader can reproduce.
    """
    merged: list[tuple[int, int]] = []
    for offset, length in ranges:
        if merged and merged[-1][0] + merged[-1][1] == offset:
            merged[-1] = (merged[-1][0], merged[-1][1] + length)
        else:
            merged.append((offset, length))
    return merged


def storage(sds, shape: list[int], element_size: int) -> dict[str, Any]:
    flag, chunk_shape = hdf4lib.chunk_info(sds, len(shape))
    try:
        coder_code = sds.getcompress()[0]
    except HDF4Error:
        coder_code = 0
    out: dict[str, Any] = {"coder": CODERS.get(coder_code, f"unknown-{coder_code}")}
    if chunk_shape is not None:
        grid = [math.ceil(n / c) for n, c in zip(shape, chunk_shape)]
        ranges = []
        for coord in itertools.product(*(range(n) for n in grid)):
            for offset, length in merge_adjacent(hdf4lib.sd_ranges(sds, coord)):
                ranges.append({"chunk": list(coord), "offset": offset, "length": length})
        out |= {"layout": "chunked", "chunk_shape": chunk_shape, "chunk_grid": grid}
    else:
        ranges = [
            {"chunk": [0] * len(shape), "offset": offset, "length": length}
            for offset, length in merge_adjacent(hdf4lib.sd_ranges(sds))
        ]
        out |= {"layout": "unchunked"}
    out["ranges"] = ranges
    out["stored_bytes"] = sum(r["length"] for r in ranges)
    out["decoded_bytes"] = int(np.prod(shape)) * element_size if shape else element_size
    return out


def describe_sds(sd: SD) -> list[dict[str, Any]]:
    out = []
    for index in range(sd.info()[0]):
        sds = sd.select(index)
        name, rank, dims, type_code, _nattrs = sds.info()
        shape = list(dims) if isinstance(dims, list) else [dims]
        type_name, dtype, order = number_type(type_code)
        dimensions = []
        for axis in range(rank):
            dim_name, dim_length, _scale_type, _n = sds.dim(axis).info()
            dimensions.append({"name": dim_name, "unlimited": dim_length == 0})
        try:
            fill = jsonable(sds.getfillvalue())
        except HDF4Error:
            fill = None
        values = read_values(sds, shape, type_code)
        entry: dict[str, Any] = {
            "index": index,
            "ref": sds.ref(),
            "name": name,
            "number_type": type_name,
            "numpy_dtype": dtype,
            "stored_byte_order": order,
            "shape": shape,
            "dimensions": dimensions,
            "is_coordinate": bool(sds.iscoordvar()),
            "fill_value_attribute": fill,
            "attributes": attributes(sds),
            "values_sha256": hashlib.sha256(canonical_bytes(values)).hexdigest(),
            "storage": storage(sds, shape, np.dtype(dtype).itemsize),
        }
        if values.size <= INLINE_LIMIT:
            entry["values"] = jsonable(values)
        out.append(entry)
        sds.endaccess()
    return out


def describe_vdata(path: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    hdf = HDF(path)
    vs = hdf.vstart()
    tables, internal = [], {}
    for name, cls, ref, nrec, nfields, _nattrs, recsize, _tag, interlace in vs.vdatainfo(1):
        if cls.startswith(INTERNAL_VDATA_CLASSES):
            internal[cls] = internal.get(cls, 0) + 1
            continue
        vd = vs.attach(ref)
        fields = [
            {"name": fname, "type": number_type(ftype)[0], "order": order}
            for fname, ftype, order, _na, _idx, _esize, _isize in vd.fieldinfo()
        ]
        records = vd.read(nrec) if nrec else []
        encoded = json.dumps(jsonable(records), separators=(",", ":"), sort_keys=True)
        table: dict[str, Any] = {
            "ref": ref,
            "name": name,
            "class": cls,
            "records": nrec,
            "record_bytes": recsize,
            "interlace": "full" if interlace == HC.FULL_INTERLACE else "none",
            "fields": fields,
            "records_json_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            "ranges": [
                {"offset": offset, "length": length}
                for offset, length in merge_adjacent(hdf4lib.vs_ranges(vd._id))
            ],
        }
        if nrec * max(nfields, 1) <= INLINE_LIMIT:
            table["rows"] = jsonable(records)
        tables.append(table)
        vd.detach()
    vs.end()
    hdf.close()
    return tables, internal


def describe_vgroups(path: str, sd: SD) -> list[dict[str, Any]]:
    """User-visible Vgroups with their members, SDS members given by dataset index."""
    hdf = HDF(path)
    v = hdf.vgstart()
    vs = hdf.vstart()
    groups = []
    ref = -1
    while True:
        try:
            ref = v.getid(ref)
        except HDF4Error:
            break
        vg = v.attach(ref)
        if vg._class not in INTERNAL_VGROUP_CLASSES:
            members = []
            for tag, member_ref in vg.tagrefs():
                if tag == HC.DFTAG_VG:
                    members.append({"kind": "vgroup", "ref": member_ref})
                elif tag == HC.DFTAG_VH:
                    members.append({"kind": "vdata", "ref": member_ref})
                elif tag == HC.DFTAG_NDG:
                    members.append({"kind": "sds", "index": sd.reftoindex(member_ref)})
                else:
                    members.append({"kind": "other", "tag": tag, "ref": member_ref})
            groups.append(
                {"ref": ref, "name": vg._name, "class": vg._class, "members": members}
            )
        vg.detach()
    vs.end()
    v.end()
    hdf.close()
    return groups


def parse_odl(text: str) -> dict[str, Any]:
    """Parse the ODL subset used by HDF-EOS2 StructMetadata into nested dicts."""
    root: dict[str, Any] = {}
    stack = [root]
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "END":
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key in ("GROUP", "OBJECT"):
            child: dict[str, Any] = {}
            stack[-1][value] = child
            stack.append(child)
        elif key in ("END_GROUP", "END_OBJECT"):
            if len(stack) > 1:
                stack.pop()
        elif value:
            stack[-1][key] = value
    return root


def describe_eos(sd: SD) -> dict[str, Any] | None:
    attrs = sd.attributes()
    parts = sorted(
        (k for k in attrs if k.startswith("StructMetadata.")),
        key=lambda k: int(k.rsplit(".", 1)[1]),
    )
    if not parts:
        return None
    text = "".join(str(attrs[k]) for k in parts)
    parsed = parse_odl(text)

    def names(structure: str, key: str) -> list[str]:
        section = parsed.get(structure, {})
        return [
            g[key].strip('"') for g in section.values() if isinstance(g, dict) and key in g
        ]

    return {
        "derived_by": "tools/describe.py from the StructMetadata attribute text",
        "struct_metadata_sha256": hashlib.sha256(text.encode("latin-1", "replace")).hexdigest(),
        "grids": names("GridStructure", "GridName"),
        "swaths": names("SwathStructure", "SwathName"),
        "points": names("PointStructure", "PointName"),
        "struct_metadata": parsed,
    }


def describe(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    hdf = HDF(str(path))
    major, minor, release, version_text = hdf.getfileversion()
    hdf.close()
    sd = SD(str(path))
    global_attributes = attributes(sd)
    # StructMetadata is reported under "eos"; keeping the text twice helps nobody.
    eos = describe_eos(sd)
    tables, internal_vdata = describe_vdata(str(path))
    result = {
        "format": FORMAT,
        "file": {
            "name": path.name,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "written_by": {"version": f"{major}.{minor}.{release}", "text": version_text},
        },
        "oracle": {"library": hdf4lib.library_version(), "tool": "pyhdf " + pyhdf_version()},
        "global_attributes": global_attributes,
        "sds": describe_sds(sd),
        "vdata": tables,
        "internal_vdata_classes": internal_vdata,
        "vgroups": describe_vgroups(str(path), sd),
        "eos": eos,
        "descriptors": ddscan.summary(raw),
    }
    sd.end()
    return result


def pyhdf_version() -> str:
    from importlib.metadata import version

    return version("pyhdf")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--stdout", action="store_true", help="print instead of writing")
    args = parser.parse_args()
    for path in args.files:
        text = json.dumps(describe(path), indent=1, allow_nan=False) + "\n"
        if args.stdout:
            sys.stdout.write(text)
        else:
            target = path.with_suffix(".expected.json")
            target.write_text(text)
            print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
