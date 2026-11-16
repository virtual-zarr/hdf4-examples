"""Checks on the published catalogue that need no HDF4 files and no C library."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog" / "v1"
EXPECTED_SCHEMA = json.loads((ROOT / "schemas/expected/v1.json").read_text())
EXTENSION_SCHEMA = json.loads(
    (ROOT / "schemas/hdf4-stac-extension/v0.1.0/schema.json").read_text()
)
ROWS = json.loads((CATALOG / "items.json").read_text())
IDS = [f"{r['collection']}/{r['id']}" for r in ROWS]


def load(relative: str) -> dict:
    return json.loads((CATALOG / relative).read_text())


def test_item_ids_are_unique() -> None:
    assert len(IDS) == len(set(IDS))


def test_every_collection_is_linked_from_the_root() -> None:
    children = {
        link["href"].split("/")[1]
        for link in load("catalog.json")["links"]
        if link["rel"] == "child"
    }
    assert children == {r["collection"] for r in ROWS}


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_expected_matches_schema(row: dict) -> None:
    errors = list(Draft7Validator(EXPECTED_SCHEMA).iter_errors(load(row["expected"])))
    assert not errors, [e.message for e in errors[:3]]


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_item_matches_extension_schema(row: dict) -> None:
    errors = list(Draft7Validator(EXTENSION_SCHEMA).iter_errors(load(row["item"])))
    assert not errors, [e.message for e in errors[:3]]


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_item_is_consistent(row: dict) -> None:
    item = load(row["item"])
    folder = (CATALOG / row["item"]).parent
    expected = load(row["expected"])

    # Relative links resolve inside the catalogue.
    for link in item["links"]:
        if not link["href"].startswith("http"):
            assert (folder / link["href"]).exists(), link

    # Metadata assets are present and match their recorded size and checksum.
    for key, asset in item["assets"].items():
        if key == "data":
            continue
        raw = (folder / asset["href"]).read_bytes()
        assert asset["file:size"] == len(raw)
        assert asset["file:checksum"] == "1220" + hashlib.sha256(raw).hexdigest()

    # The data asset, the answer key and the flat row describe the same bytes.
    data = item["assets"]["data"]
    assert data["file:size"] == expected["file"]["size"] == row["size"]
    assert data["file:checksum"] == "1220" + expected["file"]["sha256"]
    assert row["sha256"] == expected["file"]["sha256"]
    assert Path(data["href"]).name == expected["file"]["name"]


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_ranges_are_inside_the_file_and_do_not_overlap(row: dict) -> None:
    expected = load(row["expected"])
    size = expected["file"]["size"]
    spans = []
    for var in expected["sds"]:
        storage = var["storage"]
        assert storage["stored_bytes"] == sum(r["length"] for r in storage["ranges"])
        for r in storage["ranges"]:
            assert r["offset"] + r["length"] <= size
            spans.append((r["offset"], r["offset"] + r["length"]))
        if storage["coder"] == "none" and storage["layout"] == "unchunked" and storage["ranges"]:
            # Uncompressed, unchunked data is stored at exactly its decoded size.
            assert storage["stored_bytes"] == storage["decoded_bytes"]
    spans.sort()
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert end <= start, "two datasets claim the same bytes"
