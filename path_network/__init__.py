from __future__ import annotations

from pathlib import Path
import os
from typing import Any

from flask import Flask

from path_network import db, dev
from path_network.web import web


MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_REQUEST_SIZE = 50 * 1024 * 1024


def create_app(
    test_config: dict[str, Any] | None = None,
    *,
    instance_path: str | Path | None = None,
) -> Flask:
    project_root = Path(__file__).resolve().parent.parent
    flask_options: dict[str, Any] = {
        "instance_relative_config": True,
        "template_folder": str(project_root / "templates"),
        "static_folder": str(project_root / "static"),
    }
    if instance_path is not None:
        flask_options["instance_path"] = str(instance_path)

    app = Flask(__name__, **flask_options)
    app.config.from_mapping(
        MAX_CONTENT_LENGTH=MAX_REQUEST_SIZE,
        GPX_MAX_FILE_SIZE=MAX_FILE_SIZE,
        DATA_DIR=str(Path(app.instance_path) / "data"),
        DATABASE=str(Path(app.instance_path) / "path-network.sqlite"),
        INIT_DATABASE=True,
        OSM_TILE_URL=os.environ.get("OSM_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
        OSM_ATTRIBUTION=os.environ.get("OSM_ATTRIBUTION", "&copy; OpenStreetMap contributors"),
        OSM_MAX_ZOOM=int(os.environ.get("OSM_MAX_ZOOM", "19")),
        EDITOR_PASSWORD=os.environ.get("EDITOR_PASSWORD", "busmap"),
        SECRET_KEY=os.environ.get("SECRET_KEY", "busmap-local-development-key"),
    )

    if test_config:
        app.config.from_mapping(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["DATA_DIR"]).mkdir(parents=True, exist_ok=True)
    app.extensions["edit_sessions"] = {}

    db.init_app(app)
    dev.init_app(app)
    app.register_blueprint(web)
    return app
