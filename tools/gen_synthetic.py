"""Generate the synthetic collection: small HDF4 files, one storage feature each.

Every file is written by the HDF4 C library through pyhdf, so each is a valid
file by construction. Run from the repository root:

    uv run tools/gen_synthetic.py            # all cases, into data/synthetic/
    uv run tools/gen_synthetic.py --list
    uv run tools/gen_synthetic.py chunked-deflate-3d

The library records the path it was given as the name of the file's root
Vgroup, so each file is written from inside its own directory under its bare
file name. That keeps the bytes independent of where the repository lives.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pyhdf.V  # noqa: F401  (adds vgstart to HDF)
import pyhdf.VS  # noqa: F401  (adds vstart to HDF)
from pyhdf.HDF import HC, HDF
from pyhdf.SD import SD, SDC

import hdf4lib

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "synthetic"

CASES: dict[str, tuple[Callable[[str], None], str]] = {}

# Cases whose bytes differ from one machine to the next. The Skipping-Huffman
# encoder writes some bytes it never initialises, so the decoded values are
# always the same but the file is not. The described copy of each is kept in
# pinned/synthetic/ and copied from there, so every build publishes the same bytes.
NOT_REPRODUCIBLE = frozenset({"skphuff-int16"})


def case(name: str, description: str):
    def register(func: Callable[[str], None]) -> Callable[[str], None]:
        CASES[name] = (func, description)
        return func

    return register


@contextlib.contextmanager
def new_sd(filename: str) -> Iterator[SD]:
    sd = SD(filename, SDC.WRITE | SDC.CREATE | SDC.TRUNC)
    try:
        yield sd
    finally:
        sd.end()


def ramp(shape: tuple[int, ...], dtype: str) -> np.ndarray:
    return np.arange(int(np.prod(shape)), dtype=dtype).reshape(shape)


# --- contiguous layouts -----------------------------------------------------


@case("contiguous-int16", "Uncompressed contiguous int16, the simplest layout")
def _(f: str) -> None:
    with new_sd(f) as sd:
        v = sd.create("v", SDC.INT16, (3, 4))
        v[:] = ramp((3, 4), "i2")
        v.endaccess()


@case("contiguous-sliced-writes", "Uncompressed contiguous data written in 40 slices")
def _(f: str) -> None:
    data = np.random.default_rng(0).integers(0, 30000, (400, 50)).astype("i2")
    with new_sd(f) as sd:
        v = sd.create("v", SDC.INT16, (400, 50))
        for i in range(0, 400, 10):
            v[i : i + 10] = data[i : i + 10]
        v.endaccess()


@case("deflate-float32", "Contiguous deflate-compressed float32 (byte order matters)")
def _(f: str) -> None:
    with new_sd(f) as sd:
        v = sd.create("v", SDC.FLOAT32, (3, 4))
        v.setcompress(SDC.COMP_DEFLATE, 6)
        v[:] = ramp((3, 4), "f4") + 0.5
        v.endaccess()


@case("deflate-float64", "Contiguous deflate-compressed float64")
def _(f: str) -> None:
    with new_sd(f) as sd:
        v = sd.create("v", SDC.FLOAT64, (3, 4))
        v.setcompress(SDC.COMP_DEFLATE, 6)
        v[:] = ramp((3, 4), "f8") / 7
        v.endaccess()


@case("rle-int16", "Contiguous run-length-encoded int16")
def _(f: str) -> None:
    with new_sd(f) as sd:
        v = sd.create("v", SDC.INT16, (8, 8))
        v.setcompress(SDC.COMP_RLE)
        v[:] = ramp((8, 8), "i2") // 8
        v.endaccess()


@case("skphuff-int16", "Contiguous Skipping-Huffman-compressed int16")
def _(f: str) -> None:
    with new_sd(f) as sd:
        v = sd.create("v", SDC.INT16, (8, 8))
        v.setcompress(SDC.COMP_SKPHUFF, 2)
        v[:] = ramp((8, 8), "i2") // 8
        v.endaccess()


# --- chunked layouts --------------------------------------------------------


@case("chunked-uncompressed", "Chunked, no compression, 4 full chunks")
def _(f: str) -> None:
    with new_sd(f) as sd:
        v = sd.create("v", SDC.INT16, (4, 6))
        hdf4lib.set_chunk(v, [2, 3])
        v[:] = ramp((4, 6), "i2")
        v.endaccess()


@case("chunked-deflate-3d", "Chunked deflate, rank 3")
def _(f: str) -> None:
    with new_sd(f) as sd:
        v = sd.create("v", SDC.INT32, (2, 4, 6))
        hdf4lib.set_chunk(v, [1, 2, 4], deflate_level=6)
        v[:] = ramp((2, 4, 6), "i4")
        v.endaccess()


@case("chunked-deflate-edge", "Chunked deflate where the shape is not a multiple of the chunk")
def _(f: str) -> None:
    with new_sd(f) as sd:
        v = sd.create("v", SDC.INT16, (5, 7))
        hdf4lib.set_chunk(v, [2, 3], deflate_level=6)
        v[:] = ramp((5, 7), "i2")
        v.endaccess()


@case("chunked-deflate-partial", "Chunked deflate with 2 of 4 chunks written and a fill value")
def _(f: str) -> None:
    with new_sd(f) as sd:
        v = sd.create("v", SDC.INT16, (4, 6))
        v.setfillvalue(-9)
        hdf4lib.set_chunk(v, [2, 3], deflate_level=6)
        v[0:2, 0:3] = np.ones((2, 3), "i2")
        v[2:4, 3:6] = 2 * np.ones((2, 3), "i2")
        v.endaccess()


@case("chunked-deflate-single", "Chunked deflate with one chunk (chunk table not in linked blocks)")
def _(f: str) -> None:
    with new_sd(f) as sd:
        v = sd.create("v", SDC.INT16, (2, 3))
        hdf4lib.set_chunk(v, [2, 3], deflate_level=6)
        v[:] = ramp((2, 3), "i2")
        v.endaccess()


# --- linked blocks ----------------------------------------------------------


@case("unlimited-one-block", "Unlimited dimension whose data fits in one linked block")
def _(f: str) -> None:
    with new_sd(f) as sd:
        v = sd.create("v", SDC.INT16, (SDC.UNLIMITED, 4))
        for i in range(5):
            v[i] = np.arange(4, dtype="i2") + i * 10
        v.endaccess()


@case(
    "unlimited-interleaved",
    "Two unlimited datasets appended alternately, so each spans scattered linked blocks",
)
def _(f: str) -> None:
    with new_sd(f) as sd:
        a = sd.create("a", SDC.INT32, (SDC.UNLIMITED, 64))
        b = sd.create("b", SDC.INT32, (SDC.UNLIMITED, 64))
        hdf4lib.set_block_size(a, 512)
        hdf4lib.set_block_size(b, 512)
        for i in range(40):
            a[i] = np.arange(64, dtype="i4") + i * 1000
            b[i] = -(np.arange(64, dtype="i4") + i * 1000)
        a.endaccess()
        b.endaccess()


# --- datasets with no stored data -------------------------------------------


@case("never-written", "Datasets declared but never written, with and without a fill value")
def _(f: str) -> None:
    with new_sd(f) as sd:
        sd.create("default_fill", SDC.INT16, (3, 4)).endaccess()
        w = sd.create("explicit_fill", SDC.INT16, (5,))
        w.setfillvalue(-7)
        w.endaccess()
        sd.create("float_default_fill", SDC.FLOAT32, (2, 2)).endaccess()


# --- number types -----------------------------------------------------------

_TYPES = {
    "int8": (SDC.INT8, "i1"),
    "uint8": (SDC.UINT8, "u1"),
    "int16": (SDC.INT16, "i2"),
    "uint16": (SDC.UINT16, "u2"),
    "int32": (SDC.INT32, "i4"),
    "uint32": (SDC.UINT32, "u4"),
    "float32": (SDC.FLOAT32, "f4"),
    "float64": (SDC.FLOAT64, "f8"),
}


@case("all-number-types", "One contiguous dataset per big-endian number type")
def _(f: str) -> None:
    with new_sd(f) as sd:
        for name, (code, dtype) in _TYPES.items():
            v = sd.create(name, code, (2, 3))
            data = ramp((2, 3), dtype)
            if np.dtype(dtype).kind == "i":
                data = data - 3
            if np.dtype(dtype).kind == "f":
                data = data / 3
            v[:] = data
            v.endaccess()


@case("little-endian-types", "Datasets stored little-endian (DFNT_LITEND)")
def _(f: str) -> None:
    with new_sd(f) as sd:
        for name in ("int16", "int32", "float32", "float64"):
            code, dtype = _TYPES[name]
            v = sd.create(name, code | hdf4lib.DFNT_LITEND, (2, 3))
            hdf4lib.write_all(v, np.ascontiguousarray(ramp((2, 3), dtype) + 1))
            v.endaccess()


@case("char-dataset", "A char8 dataset and a uchar8 attribute")
def _(f: str) -> None:
    with new_sd(f) as sd:
        v = sd.create("v", SDC.CHAR, (4,))
        v.setcompress(SDC.COMP_DEFLATE, 6)
        v[:] = np.frombuffer(b"abcd", "S1")
        v.attr("degree_c").set(SDC.UCHAR, [0xB0, 0x43])
        v.endaccess()


# --- attributes and dimensions ----------------------------------------------

_ODL = (
    "GROUP = INVENTORYMETADATA\n"
    "  OBJECT = SHORTNAME\n    NUM_VAL = 1\n    VALUE = \"MOD 14\"\n  END_OBJECT = SHORTNAME\n"
    "  OBJECT = LIST\n    NUM_VAL = 2\n    VALUE = (\"a\", \"b\")\n  END_OBJECT = LIST\n"
    "END_GROUP = INVENTORYMETADATA\nEND\n"
)


@case("attributes", "Scalar, array, string and ODL attributes on a dataset and the file")
def _(f: str) -> None:
    with new_sd(f) as sd:
        v = sd.create("v", SDC.UINT8, (2, 2))
        v.setcompress(SDC.COMP_DEFLATE, 6)
        v[:] = ramp((2, 2), "u1")
        v.attr("valid_range").set(SDC.INT16, [0, 100])
        v.long_name = "two words"
        v.quoted = '"quoted"'
        v.attr("scale_factor").set(SDC.FLOAT64, 0.01)
        v.attr("add_offset").set(SDC.FLOAT32, 1.5)
        v.attr("_FillValue").set(SDC.UINT8, 255)
        v.endaccess()
        sd.title = "a global title"
        sd.attr("levels").set(SDC.INT32, [7, 8, 9])
        sd.attr("CoreMetadata.0").set(SDC.CHAR, _ODL)


@case("named-dimensions", "Two datasets sharing named dimensions, one with a dimension scale")
def _(f: str) -> None:
    with new_sd(f) as sd:
        for name in ("t1", "t2"):
            v = sd.create(name, SDC.INT16, (3, 4))
            v.setcompress(SDC.COMP_DEFLATE, 6)
            v.dim(0).setname("lat")
            v.dim(1).setname("lon")
            v[:] = ramp((3, 4), "i2")
            if name == "t1":
                v.dim(0).setscale(SDC.FLOAT64, [10.0, 20.0, 30.0])
            v.endaccess()


@case("duplicate-names", "Two datasets with the same name, which HDF4 allows")
def _(f: str) -> None:
    with new_sd(f) as sd:
        for offset in (0, 100):
            v = sd.create("v", SDC.INT16, (2, 2))
            v[:] = ramp((2, 2), "i2") + offset
            v.endaccess()


@case("zero-length", "A dataset with a zero-length dimension beside a normal one")
def _(f: str) -> None:
    with new_sd(f) as sd:
        sd.create("empty", SDC.FLOAT32, (0,)).endaccess()
        v = sd.create("v", SDC.INT16, (2, 2))
        v[:] = ramp((2, 2), "i2")
        v.endaccess()


# --- other objects in the file ----------------------------------------------


@case("vdata-and-vgroup", "A dataset, then a Vdata table and an empty Vgroup created after it")
def _(f: str) -> None:
    with new_sd(f) as sd:
        v = sd.create("v", SDC.INT16, (2, 2))
        v.setcompress(SDC.COMP_DEFLATE, 6)
        v[:] = ramp((2, 2), "i2")
        v.endaccess()
    h = HDF(f, HC.WRITE)
    vs = h.vstart()
    table = vs.create("table", (("x", HC.INT16, 1), ("y", HC.FLOAT32, 1), ("tag", HC.CHAR8, 4)))
    table.write([[1, 1.5, "abcd"], [2, 2.5, "efgh"], [3, 3.5, "ijkl"]])
    table.detach()
    vg = h.vgstart()
    vg.create("extra_group").detach()
    vg.end()
    vs.end()
    h.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="cases to generate (default: all)")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="write machine-dependent cases afresh instead of copying them from pinned/",
    )
    args = parser.parse_args()
    if args.list:
        for name, (_, description) in CASES.items():
            print(f"{name:28} {description}")
        return 0
    unknown = set(args.names) - set(CASES)
    if unknown:
        parser.error(f"unknown case(s): {sorted(unknown)}")
    names = args.names or list(CASES)
    if len(names) > 1:
        # One process per case. Some encoders (Skipping-Huffman) leave bytes in the
        # file that depend on what the process did before, so a case generated
        # alone and the same case generated after others would differ.
        failed = 0
        for name in names:
            failed += subprocess.run(
                [sys.executable, __file__, name, "--out", str(args.out)]
                + (["--regenerate"] if args.regenerate else []),
                check=False,
            ).returncode
        return 1 if failed else 0
    failed = 0
    name = names[0]
    func, _ = CASES[name]
    directory = args.out.resolve() / name
    directory.mkdir(parents=True, exist_ok=True)
    pinned = ROOT / "pinned" / "synthetic" / f"{name}.hdf"
    os.chdir(directory)
    try:
        if name in NOT_REPRODUCIBLE and pinned.exists() and not args.regenerate:
            shutil.copyfile(pinned, f"{name}.hdf")
            print(f"ok    {name} (copied from pinned/, since its bytes vary between machines)")
        else:
            func(f"{name}.hdf")
            print(f"ok    {name}")
    except Exception as exc:
        failed = 1
        print(f"FAIL  {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
        with contextlib.suppress(FileNotFoundError):
            os.remove(f"{name}.hdf")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
