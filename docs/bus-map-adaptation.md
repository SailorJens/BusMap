# Bus map adaptation decisions

## Source and isolation

BusMap is a separate project. The reusable RouteManagement foundation was copied into this repository; `/Users/jusegler/Documents/Code/RouteManagement` remains unchanged.

Reused modules:

- `geometry.py`: projection, splitting, intersections, geometry keys
- `repository.py`: junctions, reusable path segments, R-tree, revision and atomic graph commit
- `edit_session.py`: saved snapshot plus deterministic operation replay, temporary IDs, Undo and stale-session checks
- Flask application factory and vendored Leaflet assets
- graph, repository, geometry, and edit-session tests

Hiking-only GPX, import-draft, and overlap-matching modules are retained temporarily as isolated legacy modules so extraction does not destabilize the proven graph code. They are absent from the public viewer and bus editor. They can be deleted after the remaining shared imports (notably distance helpers) are extracted.

## Persistence and migration

The existing `junctions` and `path_segments` tables remain. `path_segments.direction_mode` is added through an idempotent startup migration for existing databases. Normalized `bus_routes`, `route_directions`, and `route_direction_segments` tables are created idempotently by the schema initializer.

Route IDs are never embedded in segment JSON. Network revisions include routes, directions, and memberships, so any bus-data write makes older edit sessions stale.

## Editing model

Route creation, direction creation, route membership, route-aware drawing, and topology changes share one replayable session. A route drawing creates its segment and membership as one operation. Commit maps temporary junction, segment, route, and direction IDs inside one SQLite transaction.

When a segment is split, child segments retain the source `direction_mode` and the derived session expands each source membership onto both children. Commit then persists the final membership set atomically.

## Application surfaces

- `/` is a read-only public viewer.
- `/editor` is the editing workspace.
- `/api/public/*` contains read-only responses without session tokens or mutation controls.
- mutation requests require an authenticated editor cookie outside test mode.

Tile URL, attribution, and maximum zoom are configuration values. The default development configuration uses the public OSM tile endpoint and must be replaced with an appropriate provider for production traffic.

## Remaining extraction work

The bus UI does not call GPX or overlap endpoints. A later cleanup can remove those endpoints and modules after moving `haversine_distance` to the shared geometry module and pruning hiking-only regression tests.
