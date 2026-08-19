# Vendored third-party assets

## MapLibre GL JS 4.7.1

- `maplibre-gl.js` — sha256 `be9633c4d870e26fb37f1cfe5c5a77181667114003ea16207ac7850d8da8add1`
- `maplibre-gl.css` — sha256 `576b085fdd9487a65a19215328c1e086c07ce5bf6da09b666b3806d3d008dae9`

Retrieved 2026-08-18 from `https://unpkg.com/maplibre-gl@4.7.1/dist/`.
Licence: 3-Clause BSD — <https://github.com/maplibre/maplibre-gl-js/blob/v4.7.1/LICENSE.txt>

**Why this is vendored rather than loaded from a CDN.** The viewer must make no
outbound request while it is running. A CDN fetch is a request to a third party
every time the page loads, and a basemap tile fetch is one *per tile, per pan* —
each one carrying the bounding box on screen. This system's entire edge is
knowing which ground is worth looking at before anyone else does (Master §8,
"Heat is public"); streaming our viewport to a tile host gives that away for a
prettier background. There is no basemap: the map's ground is the provincial
boundary and major lakes out of `geo.gpkg`, served from `/api/context`.

Upgrading: replace both files, update the hashes above, and re-run
`python src/test_mapapi.py`, which asserts the viewer references no external
origin.
