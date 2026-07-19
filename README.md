# London Bus Map

A Flask, SQLite, and Leaflet application for editing and viewing a simplified London bus network. Physical segment geometry is stored once and shared by any number of route directions.

## Current capabilities

- Read-only public viewer at `/`
- Password-protected editor at `/editor`
- Bus routes and directional variants
- Endpoint-derived Northbound, Eastbound, Southbound, and Westbound names
- Direction-specific segment membership and traversal
- Route highlighting and segment membership inspection
- Interchanges derived from graph degree
- Drawing from empty map space, an existing node, or into an existing segment
- Segment splitting with route-membership inheritance
- Replay-based staging, Undo, Cancel, stale-session detection, and atomic Save
- Configurable OSM-compatible tile provider

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
flask --app app run --debug
```

The default local editor password is `busmap`. Set `EDITOR_PASSWORD` and `SECRET_KEY` for any deployed instance. Tile configuration is available through `OSM_TILE_URL`, `OSM_ATTRIBUTION`, and `OSM_MAX_ZOOM`.

```bash
python -m pytest
```

The original RouteManagement repository is not modified. BusMap began from a copy of its proven topology, geometry, SQLite/R-tree, edit-session, and Leaflet foundations. GPX functionality remains isolated legacy code and is not exposed in the BusMap interface.
