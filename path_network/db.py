from __future__ import annotations

import sqlite3
from pathlib import Path

import click
from flask import Flask, current_app, g
from flask.cli import with_appcontext


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        database_path = Path(current_app.config["DATABASE"])
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        g.db = connection
    return g.db


def close_db(_error=None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db() -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    connection = get_db()
    connection.executescript(schema_path.read_text(encoding="utf-8"))
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(junctions)").fetchall()
    }
    if "metadata_json" not in columns:
        connection.execute(
            "ALTER TABLE junctions ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
        )
    segment_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(path_segments)").fetchall()
    }
    if "direction_mode" not in segment_columns:
        connection.execute(
            "ALTER TABLE path_segments ADD COLUMN direction_mode TEXT NOT NULL "
            "DEFAULT 'bidirectional' CHECK (direction_mode IN "
            "('bidirectional', 'start_to_end', 'end_to_start'))"
        )
    connection.commit()


@click.command("init-db")
@with_appcontext
def init_db_command() -> None:
    init_db()
    click.echo("Initialized the path-network database.")


def init_app(app: Flask) -> None:
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)

    if app.config.get("INIT_DATABASE", True):
        with app.app_context():
            init_db()
