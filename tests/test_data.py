"""Checks that need the HDF4 files in ``data/`` and, for some, the C library.

Each test skips when the file it needs has not been fetched or generated.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog" / "v1"
DATA = ROOT / "data"
ROWS = json.loads((CATALOG / "items.json").read_text())
IDS = [f"{r['collection']}/{r['id']}" for r in ROWS]


def data_file(row: dict) -> Path:
    path = DATA / row["data"]
    if not path.exists():
        pytest.skip("file not present in data/")
    if row.get("hdf4:reproducible") is False:
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            pytest.skip("local copy was regenerated; this file's bytes vary between machines")
    return path


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_data_file_matches_recorded_checksum(row: dict) -> None:
    assert hashlib.sha256(data_file(row).read_bytes()).hexdigest() == row["sha256"]


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_answer_key_is_reproducible(row: dict) -> None:
    describe = pytest.importorskip("describe")
    fresh = describe.describe(data_file(row))
    recorded = json.loads((CATALOG / row["expected"]).read_text())
    assert json.loads(json.dumps(fresh)) == recorded


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_tags_are_reproducible(row: dict) -> None:
    import build_catalog

    expected = json.loads((CATALOG / row["expected"]).read_text())
    item = json.loads((CATALOG / row["item"]).read_text())
    for key, value in build_catalog.tags(expected).items():
        assert item["properties"][key] == value


@pytest.mark.parametrize(
    "row", [r for r in ROWS if r["collection"] == "synthetic"],
    ids=[r["id"] for r in ROWS if r["collection"] == "synthetic"],
)
def test_synthetic_file_regenerates_byte_for_byte(row: dict, tmp_path) -> None:
    pytest.importorskip("pyhdf")
    if row.get("hdf4:reproducible") is False:
        pytest.skip("this file's bytes vary between machines")
    # A fresh process per case, as tools/gen_synthetic.py itself does.
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gen_synthetic.py"), row["id"], "--out", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    raw = (tmp_path / row["id"] / f"{row['id']}.hdf").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == row["sha256"]


_RANGE = re.compile(r"<dmrpp:(?:chunk|block)\b([^>]*)>")


def dmrpp_ranges(text: str) -> set[tuple[int, int]]:
    out = set()
    for attrs in _RANGE.findall(text):
        offset = re.search(r'offset="(\d+)"', attrs)
        length = re.search(r'nBytes="(\d+)"', attrs)
        if offset and length:
            out.add((int(offset.group(1)), int(length.group(1))))
    return out


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_ranges_agree_with_dmrpp(row: dict) -> None:
    """Where OPeNDAP or NASA publish a DMR++ file, its byte ranges are a second opinion."""
    item = json.loads((CATALOG / row["item"]).read_text())
    if "dmrpp" not in item["assets"]:
        pytest.skip("no DMR++ file for this item")
    text = ((CATALOG / row["item"]).parent / item["assets"]["dmrpp"]["href"]).read_text()
    expected = json.loads((CATALOG / row["expected"]).read_text())
    ours = {
        (r["offset"], r["length"]) for v in expected["sds"] for r in v["storage"]["ranges"]
    }
    tables = {(r["offset"], r["length"]) for v in expected["vdata"] for r in v["ranges"]}
    theirs = dmrpp_ranges(text)
    assert theirs - tables == ours
