from path_network.dev import seed_sample_network
from path_network.repository import create_junction, create_path_segment


def test_empty_path_network(client):
    response = client.get("/api/path-network")

    assert response.status_code == 200
    assert response.get_json() == {
        "bounds": None,
        "junctions": [],
        "pathSegments": [],
    }


def test_populated_path_network(app, client):
    with app.app_context():
        start = create_junction(8.6821, 50.1109)
        end = create_junction(8.69, 50.114)
        create_path_segment(
            start["id"],
            end["id"],
            [[8.6821, 50.1109], [8.687, 50.112], [8.69, 50.114]],
        )

    response = client.get("/api/path-network")
    payload = response.get_json()

    assert response.status_code == 200
    assert len(payload["junctions"]) == 2
    assert len(payload["pathSegments"]) == 1
    assert payload["pathSegments"][0]["geometry"][0] == [8.6821, 50.1109]


def test_seed_helper_inserts_sample_network(app):
    with app.app_context():
        result = seed_sample_network()

    assert len(result["junctions"]) == 3
    assert len(result["pathSegments"]) == 2


def test_seed_network_cli(app):
    runner = app.test_cli_runner()

    result = runner.invoke(args=["seed-network"])

    assert result.exit_code == 0
    assert "Inserted 3 junctions and 2 path segments." in result.output
