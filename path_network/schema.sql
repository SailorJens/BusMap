PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS junctions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    longitude REAL NOT NULL CHECK (longitude >= -180 AND longitude <= 180),
    latitude REAL NOT NULL CHECK (latitude >= -90 AND latitude <= 90),
    elevation REAL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS path_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_junction_id INTEGER NOT NULL REFERENCES junctions(id),
    end_junction_id INTEGER NOT NULL REFERENCES junctions(id),
    geometry_json TEXT NOT NULL,
    bounds_min_lon REAL NOT NULL,
    bounds_min_lat REAL NOT NULL,
    bounds_max_lon REAL NOT NULL,
    bounds_max_lat REAL NOT NULL,
    distance_m INTEGER NOT NULL CHECK (distance_m >= 0),
    source_filename TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    direction_mode TEXT NOT NULL DEFAULT 'bidirectional'
        CHECK (direction_mode IN ('bidirectional', 'start_to_end', 'end_to_start')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bus_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name TEXT,
    colour TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS route_directions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bus_route_id INTEGER NOT NULL REFERENCES bus_routes(id) ON DELETE CASCADE,
    start_junction_id INTEGER REFERENCES junctions(id),
    end_junction_id INTEGER REFERENCES junctions(id),
    custom_direction_name TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (start_junction_id IS NULL AND end_junction_id IS NULL)
        OR (start_junction_id IS NOT NULL AND end_junction_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS route_directions_bus_route_idx
ON route_directions(bus_route_id);

CREATE TABLE IF NOT EXISTS route_direction_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_direction_id INTEGER NOT NULL REFERENCES route_directions(id) ON DELETE CASCADE,
    path_segment_id INTEGER NOT NULL REFERENCES path_segments(id) ON DELETE CASCADE,
    traversal TEXT NOT NULL DEFAULT 'both'
        CHECK (traversal IN ('both', 'start_to_end', 'end_to_start')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(route_direction_id, path_segment_id)
);

CREATE INDEX IF NOT EXISTS route_direction_segments_segment_idx
ON route_direction_segments(path_segment_id);

CREATE INDEX IF NOT EXISTS path_segments_start_junction_idx
ON path_segments(start_junction_id);

CREATE INDEX IF NOT EXISTS path_segments_end_junction_idx
ON path_segments(end_junction_id);

CREATE VIRTUAL TABLE IF NOT EXISTS path_segment_bounds USING rtree(
    path_segment_id,
    min_lon,
    max_lon,
    min_lat,
    max_lat
);
