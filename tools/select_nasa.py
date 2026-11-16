"""Pick one small granule per collection in ``sources/nasa-selection.txt``.

Queries NASA's public Common Metadata Repository (no login needed), takes the
smallest of up to 200 granules in each collection that has an HTTPS download
link ending in an HDF4 extension and is under the size cap, and writes
``sources/nasa-<provider>.json`` with the download URL, time, footprint, DOI
and licence text recorded by NASA.

    uv run tools/select_nasa.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CMR = "https://cmr.earthdata.nasa.gov/search"
SIZE_CAP_MB = 50
HDF_SUFFIXES = (".hdf", ".HDF", ".h4", ".hdf4")
PROVIDER_TITLES = {
    "LPCLOUD": "LP DAAC",
    "LARC_CLOUD": "ASDC",
    "GHRC_DAAC": "GHRC DAAC",
    "ORNL_CLOUD": "ORNL DAAC",
    "NSIDC_CPRD": "NSIDC DAAC",
}


def get(path: str, **params: str | int) -> dict:
    url = f"{CMR}/{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "hdf4-examples-select"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def collection_record(provider: str, short_name: str, version: str) -> dict | None:
    found = get("collections.umm_json", provider=provider, short_name=short_name, version=version)
    return found["items"][0] if found["items"] else None


def size_mb(umm: dict) -> float | None:
    for info in umm.get("DataGranule", {}).get("ArchiveAndDistributionInformation", []):
        if info.get("Name", "").endswith(HDF_SUFFIXES) or len(
            umm["DataGranule"]["ArchiveAndDistributionInformation"]
        ) == 1:
            if "SizeInBytes" in info:
                return info["SizeInBytes"] / 1e6
            if "Size" in info:
                unit = {"KB": 1e-3, "MB": 1, "GB": 1e3}.get(info.get("SizeUnit", "MB"), 1)
                return info["Size"] * unit
    return None


def pick_granule(concept_id: str) -> dict | None:
    found = get("granules.umm_json", collection_concept_id=concept_id, page_size=200)
    best = None
    for item in found["items"]:
        umm = item["umm"]
        urls = [
            u["URL"]
            for u in umm.get("RelatedUrls", [])
            if u.get("Type") == "GET DATA" and u["URL"].startswith("https://") and u["URL"].endswith(HDF_SUFFIXES)
        ]
        size = size_mb(umm)
        if not urls or size is None or size <= 0 or size > SIZE_CAP_MB:
            continue
        if best is None or size < best[0]:
            best = (size, item, urls[0])
    if best is None:
        return None
    _, item, url = best
    umm = item["umm"]
    dmrpp = [u["URL"] for u in umm.get("RelatedUrls", []) if u["URL"].startswith("https://") and u["URL"].endswith(".dmrpp")]
    time = umm.get("TemporalExtent", {})
    start = time.get("RangeDateTime", {}).get("BeginningDateTime") or time.get("SingleDateTime")
    end = time.get("RangeDateTime", {}).get("EndingDateTime")
    bbox = None
    geometry = umm.get("SpatialExtent", {}).get("HorizontalSpatialDomain", {}).get("Geometry", {})
    rects = geometry.get("BoundingRectangles")
    if rects:
        r = rects[0]
        bbox = [r["WestBoundingCoordinate"], r["SouthBoundingCoordinate"], r["EastBoundingCoordinate"], r["NorthBoundingCoordinate"]]
    elif geometry.get("GPolygons"):
        points = geometry["GPolygons"][0]["Boundary"]["Points"]
        lons, lats = [p["Longitude"] for p in points], [p["Latitude"] for p in points]
        bbox = [min(lons), min(lats), max(lons), max(lats)]
    return {
        "granule_concept_id": item["meta"]["concept-id"],
        "granule_ur": umm["GranuleUR"],
        "url": url,
        "dmrpp": dmrpp[0] if dmrpp else None,
        "start": start,
        "end": end,
        "bbox": bbox,
    }


def terms_of_use(umm: dict) -> dict:
    """The collection's terms exactly as NASA records them in CMR, with nothing inferred.

    ``UseConstraints`` holds a licence URL or a licence text; ``AccessConstraints``
    sometimes adds conditions in prose. A collection may record neither.
    """
    use = umm.get("UseConstraints") or {}
    access = umm.get("AccessConstraints") or {}
    url = use.get("LicenseURL") or {}

    def clean(text: str | None) -> str | None:
        text = " ".join((text or "").split())
        return text if text and text.lower() != "none" else None

    return {
        "source": "UseConstraints and AccessConstraints of the collection record in NASA CMR",
        "license_url": url.get("Linkage"),
        "license_name": url.get("Name"),
        "license_text": clean(use.get("LicenseText")),
        "use_description": clean(use.get("Description") if isinstance(use.get("Description"), str) else url.get("Description")),
        "access_description": clean(access.get("Description")),
    }


def bbox_polygon(bbox: list[float]) -> dict:
    w, s, e, n = bbox
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args()
    selection = [
        line.split()
        for line in (ROOT / "sources" / "nasa-selection.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    by_provider: dict[str, list[dict]] = {}
    for provider, short_name, version in selection:
        record = collection_record(provider, short_name, version)
        if record is None:
            print(f"skip  {provider} {short_name}.{version}: collection not found", file=sys.stderr)
            continue
        concept_id = record["meta"]["concept-id"]
        umm = record["umm"]
        granule = pick_granule(concept_id)
        if granule is None:
            print(f"skip  {short_name}.{version}: no HDF4 granule under {SIZE_CAP_MB} MB in the first 200", file=sys.stderr)
            continue
        filename = granule["url"].rsplit("/", 1)[1]
        doi = umm.get("DOI", {}).get("DOI")
        terms = terms_of_use(umm)
        entry = {
            "id": slug(f"{short_name}-{version}"),
            "filename": filename,
            "title": f"{short_name} v{version}",
            "description": f"{umm.get('EntryTitle', short_name)}. Granule {granule['granule_ur']}.",
            "datetime": granule["start"],
            "url": granule["url"],
            "via": [
                {"href": f"https://cmr.earthdata.nasa.gov/search/concepts/{granule['granule_concept_id']}", "title": "Granule record in NASA CMR"},
                {"href": f"https://cmr.earthdata.nasa.gov/search/concepts/{concept_id}", "title": "Collection record in NASA CMR"},
            ],
            "cmr": {"collection_concept_id": concept_id, "granule_concept_id": granule["granule_concept_id"], "short_name": short_name, "version": version},
            "doi": doi,
            "terms": terms,
        }
        if terms["license_url"]:
            entry["via"].append({"rel": "license", "href": terms["license_url"], "title": terms["license_name"] or "Terms of use recorded in NASA CMR"})
        if doi:
            entry["via"].append({"href": f"https://doi.org/{doi}", "title": "Dataset DOI"})
        if granule["bbox"]:
            entry["bbox"] = granule["bbox"]
            entry["geometry"] = bbox_polygon(granule["bbox"])
        if granule["dmrpp"]:
            entry["sidecars"] = [{"key": "dmrpp", "filename": filename + ".dmrpp", "url": granule["dmrpp"], "type": "application/xml", "title": "DMR++ description published by NASA"}]
        by_provider.setdefault(provider, []).append(entry)
        print(f"ok    {provider} {short_name}.{version}: {filename}")
    for provider, items in by_provider.items():
        name = f"nasa-{slug(provider.split('_')[0])}"
        path = ROOT / "sources" / f"{name}.json"
        previous = {e["id"]: e for e in json.loads(path.read_text())["items"]} if path.exists() else {}
        for entry in items:  # keep pinned checksums when the same granule is selected again
            old = previous.get(entry["id"])
            if old and old.get("url") == entry["url"] and old.get("sha256"):
                entry["sha256"] = old["sha256"]
        source = {
            "id": name,
            "title": f"NASA HDF4 granules from {PROVIDER_TITLES.get(provider, provider)}",
            "description": f"One small granule per selected collection from NASA's {PROVIDER_TITLES.get(provider, provider)} cloud archive ({provider} in CMR), chosen to cover distinct products. Each item links to its CMR granule and collection records.",
            "license": "other",
            "license_note": "Each granule carries its collection's terms as recorded in NASA CMR; see the item's cmr:terms",
            "datetime": "2026-11-16T00:00:00Z",
            "providers": [{"name": PROVIDER_TITLES.get(provider, provider), "roles": ["producer", "licensor"]}],
            "links": [{"rel": "license", "href": "https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance", "title": "NASA Earth science data use guidance (general policy; item terms take precedence)"}],
            "items": items,
        }
        path.write_text(json.dumps(source, indent=1) + "\n")
        print(f"wrote {path.relative_to(ROOT)} with {len(items)} items")
    return 0


if __name__ == "__main__":
    sys.exit(main())
