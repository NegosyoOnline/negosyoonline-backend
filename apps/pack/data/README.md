# Pack address data

Copies of TWCinc's reference data for the Membership Pack delivery address, taken 2026-11-12 and kept byte for byte.
Read by `apps/pack/address_zip.py` (the ZIP lookup).

- `zip_codes.json`: tables `barangays`, `cities`, `cities_approx`.
  sha256 `6d25128e24b63a33844e7f5b699195e6bd555cc02cacc9de3b521e2c6612f760`.
- `provinces.json`: province names with region codes (the region label of an address).
  sha256 `1926a7f7150f7edae733fc37799d0fadb3abd3f5545d8e48c8121b1c5184ece6`.

Both are frozen. Refresh only by a reviewed commit that recopies the files and updates these hashes and the test that pins them.
The browser's province, city and barangay pickers (`city-mun.json`, `barangays.json`) are served by the storefronts app.
