"""Download the files a source lists into ``data/``, verifying or recording checksums.

    uv run tools/fetch.py gdal opendap
    uv run tools/fetch.py nasa            # needs EARTHDATA_TOKEN

Each entry in ``sources/<collection>.json`` with a ``url`` is saved as
``data/<collection>/<id>/<filename>``. If the entry already records a
``sha256`` the download must match it; if not, the hash is written back to the
source file so every later fetch is pinned to these exact bytes.

NASA downloads need an Earthdata Login token in the ``EARTHDATA_TOKEN``
environment variable. The token is sent only to ``*.nasa.gov`` hosts and is
never written to disk. In GitHub Actions, map a repository secret to it:

    env:
      EARTHDATA_TOKEN: ${{ secrets.EARTHDATA_TOKEN }}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_HOST_SUFFIX = ".nasa.gov"
USER_AGENT = "hdf4-examples-fetch"


class _TokenScopedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects, dropping the Authorization header outside NASA hosts.

    Earthdata downloads redirect through the login service and often end at a
    pre-signed storage URL on another domain, which must not see the token.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            host = urllib.parse.urlsplit(newurl).hostname or ""
            if not host.endswith(TOKEN_HOST_SUFFIX):
                new.headers.pop("Authorization", None)
                new.unredirected_hdrs.pop("Authorization", None)
        return new


def download(url: str, token: str | None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    host = urllib.parse.urlsplit(url).hostname or ""
    if token and host.endswith(TOKEN_HOST_SUFFIX):
        request.add_header("Authorization", f"Bearer {token}")
    opener = urllib.request.build_opener(_TokenScopedRedirects)
    with opener.open(request, timeout=300) as response:
        return response.read()


def fetch_collection(name: str, token: str | None, force: bool) -> int:
    source_path = ROOT / "sources" / f"{name}.json"
    source = json.loads(source_path.read_text())
    changed, failures = False, 0
    for entry in source["items"]:
        targets = [(entry.get("url"), entry.get("filename", f"{entry['id']}.hdf"), entry)]
        targets += [(s.get("url"), s["filename"], s) for s in entry.get("sidecars", [])]
        for url, filename, record in targets:
            if not url:
                continue
            path = ROOT / "data" / source["id"] / entry["id"] / filename
            if path.exists() and not force:
                raw = path.read_bytes()
            else:
                try:
                    raw = download(url, token)
                except urllib.error.HTTPError as exc:
                    hint = ""
                    if exc.code in (401, 403) and not token:
                        hint = " (set EARTHDATA_TOKEN for NASA downloads)"
                    print(f"FAIL  {entry['id']}/{filename}: HTTP {exc.code}{hint}", file=sys.stderr)
                    failures += 1
                    continue
                except (urllib.error.URLError, TimeoutError) as exc:
                    print(f"FAIL  {entry['id']}/{filename}: {exc}", file=sys.stderr)
                    failures += 1
                    continue
                if raw[:1] == b"<" and filename.endswith(".hdf"):
                    print(f"FAIL  {entry['id']}/{filename}: got an HTML page, not a file", file=sys.stderr)
                    failures += 1
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            if record.get("sha256") and record["sha256"] != digest:
                print(
                    f"FAIL  {entry['id']}/{filename}: checksum differs from the one recorded "
                    f"in {source_path.name}",
                    file=sys.stderr,
                )
                failures += 1
                continue
            if not record.get("sha256"):
                record["sha256"] = digest
                changed = True
            print(f"ok    {source['id']}/{entry['id']}/{filename} ({len(raw)} bytes)")
    if changed:
        source_path.write_text(json.dumps(source, indent=1) + "\n")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("collections", nargs="+")
    parser.add_argument("--force", action="store_true", help="download even if present")
    args = parser.parse_args()
    token = os.environ.get("EARTHDATA_TOKEN") or None
    failures = sum(fetch_collection(name, token, args.force) for name in args.collections)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
