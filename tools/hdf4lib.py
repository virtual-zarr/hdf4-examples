"""ctypes access to HDF4 C functions that pyhdf does not wrap.

pyhdf's wheel bundles libmfhdf and libdf. Loading the same shared objects again
with ctypes returns the instances pyhdf already uses, so identifiers obtained
through pyhdf (``SDS._id``, ``VD._id``) are valid here.
"""

from __future__ import annotations

import ctypes
import glob
import os

import pyhdf

I32 = ctypes.c_int32

# hdf/src/hlimits.h and mfhdf/libsrc/mfhdf.h
HDF_NONE, HDF_CHUNK, HDF_COMP, HDF_NBIT = 0, 1, 2, 4
COMP_CODE_DEFLATE = 4
DFNT_LITEND = 0x4000


def _load(stem: str) -> ctypes.CDLL:
    libs = os.path.join(os.path.dirname(pyhdf.__path__[0]), "pyhdf.libs")
    hits = glob.glob(os.path.join(libs, f"{stem}-*.so*"))
    if not hits:
        raise RuntimeError(
            f"{stem} not found beside pyhdf; this tool needs the pyhdf Linux wheel"
        )
    return ctypes.CDLL(hits[0])


mfhdf = _load("libmfhdf")
df = _load("libdf")


class ChunkDef(ctypes.Structure):
    """HDF_CHUNK_DEF, as a flat int32 array large enough for every union member.

    Layout of the ``comp`` member: chunk_lengths[32], comp_type, model_type,
    then the comp_info union, whose first int32 is the deflate level.
    """

    _fields_ = [("a", I32 * 128)]


mfhdf.SDsetchunk.argtypes = [I32, ChunkDef, I32]
mfhdf.SDgetchunkinfo.argtypes = [I32, ctypes.POINTER(ChunkDef), ctypes.POINTER(I32)]
mfhdf.SDgetdatainfo.argtypes = [
    I32,
    ctypes.POINTER(I32),
    ctypes.c_uint,
    ctypes.c_uint,
    ctypes.POINTER(I32),
    ctypes.POINTER(I32),
]
mfhdf.SDgetdatainfo.restype = ctypes.c_int
mfhdf.SDsetblocksize.argtypes = [I32, I32]
mfhdf.SDwritedata.argtypes = [
    I32,
    ctypes.POINTER(I32),
    ctypes.POINTER(I32),
    ctypes.POINTER(I32),
    ctypes.c_void_p,
]
df.VSgetdatainfo.argtypes = [
    I32,
    ctypes.c_uint,
    ctypes.c_uint,
    ctypes.POINTER(I32),
    ctypes.POINTER(I32),
]
df.VSgetdatainfo.restype = ctypes.c_int


def set_chunk(sds, lengths: list[int], deflate_level: int | None = None) -> None:
    cd = ChunkDef()
    for i, n in enumerate(lengths):
        cd.a[i] = n
    flags = HDF_CHUNK
    if deflate_level is not None:
        cd.a[32] = COMP_CODE_DEFLATE
        cd.a[34] = deflate_level
        flags |= HDF_COMP
    if mfhdf.SDsetchunk(sds._id, cd, flags) != 0:
        raise RuntimeError("SDsetchunk failed")


def set_block_size(sds, nbytes: int) -> None:
    """Set the linked-block size used when this dataset's data is appended to."""
    if mfhdf.SDsetblocksize(sds._id, nbytes) != 0:
        raise RuntimeError("SDsetblocksize failed")


def write_all(sds, data) -> None:
    """Write a whole array with SDwritedata, for number types pyhdf refuses to write.

    ``data`` is a C-contiguous numpy array in native byte order; the library
    converts it to the dataset's stored byte order.
    """
    start = (I32 * data.ndim)(*([0] * data.ndim))
    edge = (I32 * data.ndim)(*data.shape)
    if mfhdf.SDwritedata(sds._id, start, None, edge, data.ctypes.data) != 0:
        raise RuntimeError("SDwritedata failed")


def chunk_info(sds, rank: int) -> tuple[int, list[int] | None]:
    """Return (flag, chunk lengths or None when the dataset is not chunked)."""
    cd = ChunkDef()
    flag = I32(0)
    if mfhdf.SDgetchunkinfo(sds._id, ctypes.byref(cd), ctypes.byref(flag)) != 0:
        raise RuntimeError("SDgetchunkinfo failed")
    if flag.value & HDF_CHUNK:
        return flag.value, list(cd.a[:rank])
    return flag.value, None


def sd_ranges(sds, chunk: tuple[int, ...] | None = None) -> list[tuple[int, int]]:
    """Byte ranges (offset, length) holding a dataset's, or one chunk's, stored bytes."""
    coord = (I32 * len(chunk))(*chunk) if chunk is not None else None
    n = mfhdf.SDgetdatainfo(sds._id, coord, 0, 0, None, None)
    if n < 0:
        raise RuntimeError("SDgetdatainfo failed")
    if n == 0:
        return []
    off, length = (I32 * n)(), (I32 * n)()
    if mfhdf.SDgetdatainfo(sds._id, coord, 0, n, off, length) < 0:
        raise RuntimeError("SDgetdatainfo failed")
    return list(zip(off, length))


def vs_ranges(vdata_id: int) -> list[tuple[int, int]]:
    n = df.VSgetdatainfo(vdata_id, 0, 0, None, None)
    if n < 0:
        raise RuntimeError("VSgetdatainfo failed")
    if n == 0:
        return []
    off, length = (I32 * n)(), (I32 * n)()
    if df.VSgetdatainfo(vdata_id, 0, n, off, length) < 0:
        raise RuntimeError("VSgetdatainfo failed")
    return list(zip(off, length))


def library_version() -> str:
    """Version of the HDF4 C library doing the reading, e.g. ``4.2.16``."""
    major, minor, release = ctypes.c_uint32(), ctypes.c_uint32(), ctypes.c_uint32()
    text = ctypes.create_string_buffer(256)
    df.Hgetlibversion(ctypes.byref(major), ctypes.byref(minor), ctypes.byref(release), text)
    return f"{major.value}.{minor.value}.{release.value}"
