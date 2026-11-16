# Third-party content

This repository describes HDF4 files that other organisations produced. Those files, and
anything copied from those organisations, stay under their own terms. Nothing in this
repository's `LICENSE` or `LICENSE-GENERATED-DATA` changes them.

An answer key (`*.expected.json`) or STAC item written by this repository's tools is a
description of a file. It is licensed as generated data, and it does not alter the terms
of the file it describes.

## What comes from where

| Source | What is used | Where it is kept | Terms |
| --- | --- | --- | --- |
| GDAL | 21 HDF4 files from `autotest/gcore/data` and `autotest/gdrivers/data/hdf4`, pinned to commit `1fe1fe6b`; 6 real granules from `download.osgeo.org/gdal/data/hdf4/` | HDF4 files: not in this repository; downloaded into `data/` and published on Source Cooperative | GDAL's statement, below |
| OPeNDAP | 13 HDF4 files and 12 DMR++ files from `modules/dmrpp_module/data/h4_dmrpp` in `OPENDAP/bes`, pinned to commit `30bf584f` | HDF4 files: not in this repository. DMR++ files: copied unchanged into `catalog/v1/opendap/` | The BES repository's `COPYING` (GNU Lesser General Public License, version 3) and `COPYRIGHT_UCAR` |
| NASA | 24 granules, one per selected collection, from LP DAAC, ASDC, NSIDC DAAC and GHRC DAAC | HDF4 files: not in this repository; downloaded into `data/` and published on Source Cooperative | Each collection's terms as recorded in NASA's Common Metadata Repository, below |

The exact file list, download URLs and checksums are in `sources/`.

## GDAL

GDAL states, in [`autotest/README.md`](https://github.com/OSGeo/gdal/blob/1fe1fe6b8a48f1213573b7e6c6d954e4b6fb031f/autotest/README.md#notes-about-availability-of-gdal-sample-and-test-data)
and in the [`COPYING`](https://download.osgeo.org/gdal/data/COPYING) file on its data server:

> The GDAL Team makes every effort to assure that all sample data files available from
> GDAL download server (http://download.osgeo.org/gdal/data/) and test data files used in
> GDAL Autotest package are available as public and freely redistributable geodata.

The GDAL source tree is under an MIT-style licence
([`LICENSE.TXT`](https://github.com/OSGeo/gdal/blob/1fe1fe6b8a48f1213573b7e6c6d954e4b6fb031f/LICENSE.TXT)).

The six real granules are NASA products that GDAL redistributes. GDAL records an origin
for two of them in
[`PROVENANCE.TXT`](https://download.osgeo.org/gdal/data/hdf4/PROVENANCE.TXT). Their NASA
collection records have not been looked up.

## OPeNDAP

The files come from the [OPeNDAP BES repository](https://github.com/OPENDAP/bes), whose
[`COPYING`](https://github.com/OPENDAP/bes/blob/30bf584f713306c8ef867368d6840c18bafc35a6/COPYING)
file is the GNU Lesser General Public License, version 3, alongside
[`COPYRIGHT_UCAR`](https://github.com/OPENDAP/bes/blob/30bf584f713306c8ef867368d6840c18bafc35a6/COPYRIGHT_UCAR).
The repository makes no separate statement about its test data.

The DMR++ files under `catalog/v1/opendap/*/*.dmrpp` are byte-for-byte copies of
OPeNDAP's files and remain under OPeNDAP's terms.

## NASA

Each NASA item records its collection's `UseConstraints` and `AccessConstraints` exactly
as they appear in NASA's Common Metadata Repository (CMR), in the item's `cmr:terms`
property, with the dataset DOI and the CMR record ids.

| What CMR records | Collections |
| --- | --- |
| Link to <https://earthdata.nasa.gov/earth-observation-data/data-use-policy> | 11 (LP DAAC) |
| Link to <https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance> | 5 (ASDC) |
| Licence text beginning "These data are freely, openly, and fully available to use without restrictions, provided that you cite the data according to the recommended citation" | 4 (NSIDC DAAC) |
| Nothing | 4: MCD12Q1, MCD19A2, MOD28C2 (LP DAAC) and `olsana` (GHRC DAAC) |

Where CMR records nothing, `cmr:terms` is left empty. Please cite NASA datasets by the DOI
recorded on each item.
