# Architecture and algorithms

## 1. Purpose and current scope

Route Management converts GPX traces into a reusable walking path network.
Unlike a conventional GPX viewer, the application distinguishes source
geometry from graph topology:

- A **track section** and its **track points** come from a GPX file.
- A **junction** is a meaningful graph connection.
- A **path segment** is reusable geometry between two junctions.
- A **shape point** preserves the geometry inside a path segment without
  becoming a junction.

The implementation currently covers Stages 0–8, completing the planned MVP:

- Parse and validate GPX files.
- Persist and display a saved path network in SQLite.
- Convert one GPX file into proposed junctions and path segments.
- Detect crossings, proximity connections, and self-crossings.
- Stage the import in a revision-bound, in-memory edit session.
- Display saved, added, deleted, and replaced objects.
- Select path segments through an enlarged invisible map hit target.
- Project a manual click onto saved or staged path geometry and add a junction.
- Reuse an existing endpoint junction when the split is within 10 metres.
- Stage confirmed deletion of saved or newly added path segments.
- Derive pending deletion of saved junctions that become orphaned.
- Remove a staged degree-two junction and merge its incident path geometries.
- Undo imports, manual splits, deletions, or merges, or cancel the session.
- Commit the complete staged graph atomically to SQLite.

The following behavior is deliberately outside the MVP:

- Redo.
- Direct Restore independent of Undo.
- Multiple GPX imports in one session.
- Durable or multi-user edit sessions.

## 2. High-level architecture

The system is a small Flask application with a server-authoritative geometry
pipeline and a Leaflet browser client.

```text
Browser / Leaflet
       |
       | HTTP + JSON / multipart GPX
       v
Flask routes (web.py)
       |
       +--> GPX parser (gpx.py)
       |
       +--> Edit-session replay (edit_session.py)
                    |
                    +--> Import conversion (import_draft.py)
                              |
                              +--> Geometry primitives (geometry.py)
                              |
                              +--> Saved snapshot / spatial shortlist
       |
       +--> Repository (repository.py)
                    |
                    v
              SQLite + R-tree
```

The backend owns topology and geometry decisions. The browser only renders the
derived result and sends user intent. This avoids having two subtly different
intersection implementations in Python and JavaScript.

## 3. Module responsibilities

### `path_network/__init__.py`

Creates and configures the Flask application.

It also owns the current process-local edit-session store:

```python
app.extensions["edit_sessions"] = {}
```

An in-memory store is appropriate for the MVP because sessions may be lost on
refresh or process restart. It keeps Stage 4 small and avoids creating a
persistence format before the operation model settles. A production,
multi-process deployment will eventually require a shared or persistent
session store.

### `path_network/web.py`

Defines HTTP routes and translates domain errors into HTTP responses.

The route layer intentionally remains thin:

- Validate file presence and extension.
- Parse GPX input.
- Call an edit-session operation.
- Return the complete derived session.

It does not calculate intersections or mutate staged graph objects directly.

### `path_network/gpx.py`

Parses GPX XML into this internal source representation:

```text
{
  name,
  filename,
  segments: [
    [[longitude, latitude, optional_elevation], ...]
  ],
  stats
}
```

Separate GPX `trkseg` elements remain separate. Joining them would invent a
path across an intentional recording discontinuity.

Security and validation choices:

- Input is read with a strict size limit.
- DTD and entity declarations are rejected.
- The root element must be `gpx`.
- Invalid individual coordinates are skipped.
- A document with no usable track or route points is rejected.

The standard-library XML parser is sufficient for the current GPX subset and
keeps the dependency footprint small.

### `path_network/geometry.py`

Contains geometry primitives that do not depend on Flask or SQLite:

- Local metric projection.
- Bounding-box expansion.
- Point-to-leg projection.
- Leg intersection.
- Coordinate interpolation.
- Ordered geometry splitting.
- Direction-independent exact geometry keys.

Keeping these operations pure makes the numerically sensitive behavior easy to
unit test.

### `path_network/import_draft.py`

Converts parsed GPX source geometry plus a saved-network snapshot into proposed
graph changes.

Despite the historical filename, this module no longer owns draft storage. It
is a deterministic import-conversion service used by edit-session replay.

Its result identifies:

- Added junctions.
- Added imported path segments.
- Added replacement path segments.
- Saved path-segment IDs that are replaced.
- Exact duplicate sections that were skipped.

It performs no database writes.

Before ordinary crossing processing, it asks `overlap_matching.py` for
candidate intervals. Review decisions in the import operation determine which
complete sections or shared intervals are removed before the normal crossing
and splitting pass.

### `path_network/overlap_matching.py`

Implements trace-overlap reconciliation matching as pure geometry analysis:

- Resamples uploaded geometry at a regular metric interval.
- Keeps multiple nearby saved-leg projection options.
- Selects a continuous saved path chain with transition costs.
- Derives sustained candidate intervals.
- Reports separation, direction, monotonicity, ambiguity, confidence, and
  likely branch/join boundary types.
- Produces deterministic candidate keys and a versioned configuration.

The module does not itself create junctions or mutate topology. It identifies
one or more continuous candidate intervals per section, including candidates
that traverse connected saved path segments, and maps their uploaded and saved
boundary positions. Edit-session decisions determine whether
`import_draft.py` suppresses each complete section or shared interval.

### `path_network/edit_session.py`

Owns staged user intent and derives the visible network.

Each session stores:

- A random token.
- The base network revision.
- A deep-copied saved-network snapshot.
- An ordered operation list.

The derived network is recomputed from the snapshot and operations. The current
operations are `import_trace`, `split_path_segment`, `delete_path_segment`,
`merge_at_junction`, `move_junction`, and `duplicate_cleanup`.

Manual split operations store the target staged segment, projected position,
projected coordinate, and stable temporary IDs for the new junction and
replacement paths. Replay can therefore split either a saved path or geometry
added by an earlier import or split.

Deletion operations store the target staged path ID. Replay marks a saved path
as `deleted`, while a newly added path is removed from the final staged graph
without creating a database deletion. Endpoint junctions touched by deletion
are checked against the final staged degree: saved degree-zero junctions become
`deleted`, added degree-zero junctions disappear from the staged graph, and
shared junctions remain unchanged.

Merge operations store the selected junction, its two incident staged path
IDs, and a stable temporary ID for the merged path. Replay requires exactly two
active incident paths, rejects self-loops and a merged loop, orients both
geometries through the selected junction, removes their duplicate meeting
coordinate, and adds one merged path between the remaining endpoints. Saved
sources become `replaced`; added sources disappear. A saved removed junction
becomes `deleted`, while an added removed junction disappears. Source metadata
must match; common provenance is retained when both source filenames match.

Move operations store the selected staged junction, all currently incident
path IDs, a replacement junction ID, stable replacement path IDs, and the new
coordinate. Replay marks saved source objects as deleted or replaced, removes
added source objects, and adds the moved junction plus endpoint-adjusted paths.
Only the relevant first or last coordinate changes; loop paths update both.
Elevation, interior shape points, metadata, and provenance are preserved.

When placement targets another active path segment, the operation additionally
stores that target ID, the projected position, and two stable replacement path
IDs. Replay splits the target at the moved junction coordinate. This creates
one shared graph node between the moved incident paths and both target halves
without relying on a literal GPX crossing.

Duplicate-cleanup operations are explicit manual edits for existing saved
network duplicates. The implemented scope handles two saved single-segment
chains. Comparison reports separation, length ratio, traversal direction,
metadata conflicts, and how many external connections would be rewired. Replay
keeps the chosen segment, deletes the duplicate segment, replaces external
segments incident to the removed duplicate endpoints with endpoint-rewired
copies, and then lets orphan cleanup remove unused duplicate junctions.

This replay model was chosen instead of inverse mutations because Undo becomes
“remove the last operation and derive again.” It avoids maintaining a fragile
set of reverse edits for every graph transformation.

### `path_network/repository.py`

Owns SQLite reads, writes, validation, R-tree synchronization, and network
revision calculation.

Repository functions return plain dictionaries so the API and geometry layers
do not depend on SQLite row objects.

### `path_network/db.py` and `schema.sql`

Manage connection lifecycle and schema initialization.

Each Flask application context gets one SQLite connection with foreign keys
enabled. Connections are closed during context teardown.

### Browser files

- `templates/index.html` defines the editor layout.
- `static/app.js` creates sessions, uploads GPX files, renders object states,
  and invokes Undo or Cancel.
- `static/styles.css` defines the visual language and layout.

Leaflet is vendored under `static/vendor` so the core map application does not
depend on a third-party CDN at runtime.

## 4. Persistent graph model

### Junctions

A junction stores longitude, latitude, optional elevation, and a permanent
integer ID.

### Path segments

A path segment stores:

- Start and end junction IDs.
- Ordered WGS84 geometry as compact JSON.
- Cached WGS84 bounds.
- Cached distance.
- Optional source filename.
- A reserved metadata JSON object.

The complete geometry includes both endpoint coordinates:

```text
start junction -> shape points -> end junction
```

Loops are valid: start and end may reference the same junction.

### Graph invariants

Repository writes enforce the important endpoint invariant: path geometry must
begin at the start junction and end at the end junction.

Foreign keys ensure referenced junctions exist. Application logic additionally
ensures that path segments contain at least two valid coordinates.

### Spatial index

`path_segment_bounds` is an SQLite R-tree containing one bounding box per saved
path segment.

The import process first queries this index using the uploaded trace’s expanded
bounds. It then performs detailed leg comparisons only against shortlisted
segments.

This two-phase design was chosen because exact intersection checks scale with
the number of geometric legs. Comparing every upload leg with every saved leg
would become unnecessarily expensive as the network grows.

## 5. Network revisions and stale sessions

A network revision is a SHA-256 hash of a canonical JSON representation of the
saved graph. The hash includes topology, geometry, distance, provenance, and
metadata, but excludes timestamps.

Excluding timestamps is intentional: the revision should change when graph
meaning changes, not merely because a row was read or touched.

An edit session captures the revision and saved snapshot together. Every
derived response compares the current repository revision with the base
revision and sets `isStale`.

The commit endpoint rejects a session when the current revision differs from
the base revision. This is optimistic concurrency control: editing does not
lock the database, but stale work cannot silently overwrite newer changes.

## 6. GPX import algorithm

### 6.1 Normalize track sections

For every GPX section:

1. Convert coordinates to floats.
2. Remove consecutive duplicate positions.
3. Require at least two distinct positions.
4. Preserve section boundaries.
5. Detect exact duplicate sections independent of traversal direction.
6. Create mandatory endpoint cuts.

A closed section reuses one endpoint junction, allowing a valid loop.

### 6.2 Shortlist saved geometry

The uploaded geometry’s WGS84 bounds are expanded by the 10-metre tolerance.
The R-tree returns saved path segments whose bounds overlap that area.

Saved junctions are shortlisted with a conventional coordinate-range query.
The expected number of junctions in an import area is currently small. If this
becomes a bottleneck, junctions can receive their own R-tree.

### 6.3 Project to local metres

GPX coordinates are WGS84 longitude and latitude. Degrees are unsuitable for a
10-metre tolerance because longitude scale changes with latitude.

The current projector uses a local equirectangular approximation centered on
the mean coordinate:

```text
x = longitude_delta_radians * Earth_radius * cos(origin_latitude)
y = latitude_delta_radians  * Earth_radius
```

Why this approach:

- Walking imports cover small geographic areas.
- The calculations need local distances and intersections, not global
  navigation accuracy.
- It avoids a heavy geospatial dependency for the MVP.
- It is deterministic and easy to test.

This is not a general-purpose map projection. Very large traces, polar regions,
or high-accuracy surveying would justify replacing it with a proper local CRS
through a library such as PROJ.

### 6.4 Detect connections and crossings

The converter detects four classes of topology event:

1. A trace passes within 10 metres of an existing junction.
2. A trace leg genuinely crosses a saved path leg.
3. A trace endpoint stops within 10 metres of a saved path interior.
4. Trace legs cross other legs in the same upload.

Uploaded self-crossing detection excludes adjacent legs. For a closed section,
the first and last legs are also treated as adjacent so the normal loop closure
does not create a spurious extra junction.

Nearby parallel paths are not connected merely because their separation is
less than 10 metres. Proximity creates topology only for existing junction
snapping or a track-section endpoint stopping near a saved path.

### 6.5 Leg intersection

For non-parallel legs, the implementation uses the two-dimensional parametric
line intersection formula. It returns:

- Fraction along the first leg.
- Fraction along the second leg.
- Projected crossing coordinate.

Fractions are retained because the splitter needs ordered positions along each
polyline.

Parallel or collinear legs are treated carefully:

- One shared endpoint is a valid contact.
- Overlapping collinear ranges are not treated as one ordinary crossing.

Automatic overlap reconciliation is intentionally deferred. Exact full-path
duplicates are skipped, while partial overlaps require more product decisions
than a single crossing point can express.

### 6.6 Snap to existing junctions

When an event lies within 10 metres of a saved junction, the saved junction
coordinate and ID become canonical.

This prevents nearly coincident junctions and preserves existing graph
identity. Added path segments may therefore reference a mixture of permanent
saved junction IDs and temporary draft junction IDs.

### 6.7 Cluster noisy detections

GPS noise or multiple nearby saved paths can produce several detections around
one real-world crossing.

New crossing events are grouped by transitive proximity: if detections form a
chain in which neighboring points are within 10 metres, the whole connected
group becomes one cluster.

The cluster coordinate is the mean projected point. Events snapped to a saved
junction are grouped by junction ID instead, preserving the exact stored
coordinate.

Transitive grouping was chosen so the result does not depend on event
processing order.

### 6.8 Split geometry

Every geometry cut is represented as a floating position:

```text
position = leg_index + fraction_along_leg
```

Cuts are sorted once per geometry. The splitter then emits pieces between
consecutive cuts while preserving:

- Original coordinate order.
- Interior shape points.
- Inserted crossing coordinates.
- Endpoint node identity.
- Elevation when it can be interpolated from two elevated points.

Sorting all cuts before splitting avoids repeatedly modifying geometry and
accumulating numeric drift.

### 6.9 Duplicate detection

An exact geometry key:

- Uses longitude and latitude.
- Rounds to eight decimal places.
- Chooses the lexicographically smaller of forward and reverse coordinate
  order.

Elevation is excluded because graph duplication is based on horizontal path
geometry. Reverse traversal therefore compares equal, while near-parallel or
approximately matching paths remain distinct.

### 6.10 Approximate-overlap diagnostics and reviewed reuse

Overlap analysis runs before crossing derivation but does not modify its input.
The saved-path R-tree shortlist uses the wider diagnostic search corridor,
while existing 10-metre crossing and endpoint rules remain unchanged.

Uploaded sections are resampled every five metres in local projected
coordinates. Each sample retains all plausible saved-leg projections within
the search corridor. A dynamic-programming pass prefers low separation,
similar direction, monotonic progress, and transitions through shared saved
junctions while penalizing disconnected jumps.

Continuous selected runs of at least 30 metres become candidates. Diagnostics
include:

- Saved path-segment chain.
- Candidate length and section coverage.
- Median, 90th-percentile, and maximum separation.
- Direction agreement and monotonicity.
- Ambiguity from similarly close alternative paths.
- High, medium, or low confidence.
- Likely branch or join boundaries based on sustained unmatched geometry.

These thresholds are initial measurement values and are versioned as
`diagnostics-v1`. Crossings, disjoint paths, and short close runs should not
produce sustained candidates; parallel or ambiguous paths must not receive high
confidence.

Candidates on one continuous saved path chain become explicit review items
when they cover the complete section or form a usable partial interval. The
`import_trace` operation stores user overlap overrides by deterministic
candidate key:

- `reuse` removes the complete uploaded section or its shared interval before
  ordinary crossing and splitting logic, leaving the saved chain canonical.
- `keep` feeds the unchanged section through the existing importer.
- high- and medium-confidence candidates automatically reuse saved geometry,
  while low-confidence candidates automatically keep uploaded geometry.
- unresolved review items disable commit.
- resetting a user override returns the candidate to its automatic state.

User decisions are configuration inside the import operation, so Undo still
removes the complete import as one action. Re-derivation reproduces automatic
and user decisions against the revision-bound saved snapshot. A
missing or changed candidate key cannot silently apply to another interval.
Automatic decisions are included in `import.overlapAnalysis.automaticDecisions`
with local evidence for false-positive investigation; no telemetry leaves the
application.

For partial reuse, diagnostic sample positions are mapped back to floating
positions on the original GPX geometry. The importer preserves original shape
points and elevation on the unmatched prefix and suffix, replacing only each
boundary coordinate with its projection onto the saved path. Existing
intersection processing then reuses a nearby saved junction or splits the
saved boundary path at that exact coordinate. This creates one intentional
degree-three branch or join without a short diagonal GPS-noise connector.

Several non-overlapping reuse intervals per track section are supported.
Accepted intervals are sorted by uploaded geometry position and conflicting or
overlapping decisions are rejected. The importer preserves original shape
points and elevation in every unmatched gap between reused intervals, replacing
only gap endpoints with their saved-path projections. Separate GPX track
sections are composed independently.

Overlap boundary adjustments are stored in the import operation by candidate
key. A click-to-place adjustment records the requested start or end coordinate;
replay projects it onto the uploaded section, projects and snaps the
corresponding boundary onto the saved path chain, validates that start remains
before end, and then runs the same import composition as an ordinary reviewed
decision. Browser code never mutates the staged graph directly.

The API exposes diagnostics and decisions under `import.overlapAnalysis`. A
developer map overlay is enabled with `?overlapDebug=1`. The
`flask overlap-diagnostics` command evaluates the synthetic fixture corpus and
can fail when measured results differ from expectations.

Candidate splitting and manual missed-overlap comparison remain unimplemented
until later iterations.

## 7. Edit-session replay

Creating a session copies the saved network and records its revision.

Adding an import stores parsed GPX intent rather than storing a mutable derived
graph. Derivation then:

1. Starts from the saved snapshot.
2. Replays each operation.
3. Marks unchanged saved objects as `saved`.
4. Marks superseded saved path segments as `replaced`.
5. Adds temporary junctions and path segments as `added`.
6. Marks saved deletion targets and genuinely orphaned saved junctions as
   `deleted`.
7. Removes deleted additions and their added orphan junctions from the final
   staged graph.
8. Replaces merge sources with one oriented, concatenated added path and
   removes the degree-two junction.
9. Replaces moved junctions and their incident paths with endpoint-adjusted
   staged objects.
10. Calculates operation and change summaries.

Undo removes the final operation and reruns the same derivation. Re-importing
the same GPX after Undo produces the same staged topology.

A manual split first projects the browser click to the closest leg of the
selected geometry in local metric coordinates. If that point is within 10
metres of either endpoint, no operation is appended and the response asks the
browser to select the existing endpoint junction. Otherwise replay replaces a
saved target, or removes an added target, and adds one junction plus two path
segments. Shape-point order, interpolated elevation, source filename, and
metadata are preserved.

A junction merge operates only on the current staged graph. It requires degree
two, rejects source self-loops and results whose remaining endpoints are the
same, and concatenates the source coordinates after orienting the first path
toward the junction and the second away from it. This supports any combination
of forward and reversed source geometries, including paths and junctions added
by earlier operations.

A junction move also operates on the current staged graph, so a junction
created by an import or manual split can be corrected before Save. The backend
rejects positions less than one metre from the current or another active
junction and rejects any move that would leave an attached path without two
distinct horizontal coordinates. A target-path split must be an interior
position more than ten metres from either endpoint; endpoint identity merging
is not part of this operation.

Current states are:

- `saved`
- `added`
- `deleted`
- `replaced`

All four states are produced by the current import, split, deletion, and merge
operations. Deleted and replaced geometry remains visible as dashed, faded
base-colour lines until Undo, Cancel, or Save.

## 8. API flow

The browser follows this sequence:

```text
GET  /api/path-network
POST /api/edit-sessions
POST /api/edit-sessions/{token}/imports
PUT  /api/edit-sessions/{token}/imports/overlaps/{candidate_key}
DELETE /api/edit-sessions/{token}/imports/overlaps/{candidate_key}
POST /api/edit-sessions/{token}/path-segments/{id}/split
DELETE /api/edit-sessions/{token}/path-segments/{id}
DELETE /api/edit-sessions/{token}/junctions/{id}
POST /api/edit-sessions/{token}/junctions/{id}/move
POST /api/edit-sessions/{token}/undo
DELETE /api/edit-sessions/{token}
```

Each mutating session response returns the complete derived staged network.
This is intentionally less bandwidth-efficient than incremental patches, but
it gives the browser one authoritative representation and keeps UI state
recovery straightforward.

Saving uses:

```text
POST /api/edit-sessions/{token}/commit
```

On success the response contains the newly saved network and resets the same
session to an empty operation history at the new revision.

## 9. Browser rendering

The map uses separate Leaflet feature groups for saved and added objects.

Visual states do not rely on color alone:

- Saved paths are solid green.
- Added paths and junctions are solid orange.
- Replaced paths remain visible as faded, dashed green lines.
- Replaced or deleted junction styles use reduced fill and a dashed outline.

Added replacements are rendered after saved objects, placing them visually
above the paths they supersede.

Selectable paths receive a second, invisible 18-pixel Leaflet line so they are
easy to click without changing their visible width. Selection is a transient
browser overlay. The **Add junction here** action enters placement mode; the
next click on the selected path sends longitude and latitude to the split API.

The UI disables another upload while any operation is staged. The user must
Undo or Cancel first, matching the one-import-per-session MVP rule and keeping
operation ordering deterministic.

## 10. Testing strategy

Tests are divided by responsibility:

- `test_gpx.py`: parsing, validation, and statistics.
- `test_geometry.py`: projection, intersections, and splitting primitives.
- `test_import_draft.py`: topology conversion and crossing behavior.
- `test_repository.py`: persistence, invariants, and R-tree queries.
- `test_edit_session_api.py`: session lifecycle, replay, states, staleness,
  Undo, and database immutability.
- `test_app.py`: rendered page and browser contract checks.

Geometry tests include:

- X crossings.
- Self-crossings.
- Crossings between GPX sections.
- Existing-junction snapping.
- Endpoint-to-path connection.
- Nearby parallel paths.
- Multiple cuts.
- Loop splitting.
- Reverse-direction duplicates.
- Noisy crossing clustering.
- Closest-leg projection with elevation interpolation.
- Manual splitting of saved and imported path segments.
- Existing-endpoint selection and exact Undo restoration.
- Saved and added path-segment deletion.
- Orphan-junction derivation and shared-junction retention.
- Deletion Undo, Cancel, and atomic Save behavior.
- Degree-two junction merging with forward and reversed source geometry.
- Merge rejection for loops, invalid degree, and incompatible metadata.
- Merge Undo, Cancel, and atomic Save behavior.
- Saved, added, degree-three, and loop junction movement.
- Move-and-connect onto a saved or staged path with target splitting.
- Move validation, exact Undo restoration, and atomic Save behavior.

Temporary databases and instance directories isolate every test.

## 11. Known limitations

### Current limitations

- Sessions disappear when the Flask process restarts.
- A process-local session is unavailable to other server workers.
- One import operation is permitted per session.
- The local projection assumes a geographically small trace.
- Partial collinear overlaps are not reconciled.
- Several approximate overlap intervals in one GPX section cannot yet be
  composed into one reviewed import.
- The complete saved network is returned to the browser.
- Repository convenience create functions commit individually. Edit-session
  Save does not use them; it has a dedicated atomic transaction.

## 12. Atomic commit protocol

Saving persists the final derived graph rather than applying browser-generated
SQL-like commands.

Before opening the write transaction, the repository validates the staged
graph:

- Every final path has two distinct coordinates.
- Every path references visible final junctions.
- Geometry endpoints match their junction coordinates.
- Final path geometry contains no exact duplicates.

The repository then opens `BEGIN IMMEDIATE`. Taking the SQLite write lock before
the second revision check closes the race between concurrency validation and
the first mutation.

Within the transaction:

1. Recalculate and verify the saved-network revision.
2. Record explicitly deleted saved junctions and the endpoint junctions of
   deleted or replaced paths as orphan candidates.
3. Delete their R-tree rows and path rows.
4. Insert added junctions and build a temporary-to-permanent ID map.
5. Resolve every added path endpoint through either an existing integer ID or
   that temporary ID map.
6. Insert each path and its R-tree bounds.
7. Delete touched junctions that remain unreferenced.
8. Commit once.

Any exception rolls the complete transaction back. The edit session retains
its operation history after a failed commit, allowing the user to retry or
cancel.

Orphan cleanup is deliberately limited to touched junctions. This prevents a
no-op or duplicate-only import from deleting unrelated pre-existing orphan
junctions.

Replacement path segments inherit the source segment's metadata and provenance.
Imported path segments start with empty metadata and the uploaded filename as
provenance.

After a successful commit, the repository reloads the saved network and
calculates its new revision. The session then replaces its snapshot, clears its
operation history, and becomes a clean session at that revision.

## 13. Next architectural step

The MVP is complete through Stage 8, and junction movement is implemented as
the first data-cleaning enhancement. Trace-overlap reconciliation is the current
priority before Stage 9 Redo and direct Restore.
