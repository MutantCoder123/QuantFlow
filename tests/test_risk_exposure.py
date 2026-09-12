"""Test the GET /api/risk/exposure endpoint (Task 5.3)."""
from fastapi.testclient import TestClient
import api_server
from reasoning_engine import ReasoningEngine

client = TestClient(api_server.app)


def setup_function():
    """Clear user positions before each test."""
    ReasoningEngine.user_positions.clear()


def teardown_function():
    """Clear user positions after each test."""
    ReasoningEngine.user_positions.clear()


def test_exposure_endpoint_exists():
    """The endpoint should exist and return status 200."""
    res = client.get("/api/risk/exposure")
    assert res.status_code == 200
    assert res.json().get("status") == "success"


def test_empty_portfolio_returns_empty_clusters():
    """With no open positions, clusters should be an empty list, status still success."""
    res = client.get("/api/risk/exposure")
    json_data = res.json()
    assert json_data["status"] == "success"
    assert json_data["data"]["clusters"] == []
    assert "capital" in json_data["data"]
    assert "max_cluster_risk_pct" in json_data["data"]


def test_single_position_with_cluster():
    """A single position with a known cluster should appear in the response."""
    # Set up a position: SAIL (maps to PSU_METALS_INFRA cluster) with qty=100, entry=95, stop=90
    # risk_amount = 100 * |95 - 90| = 500 rupees
    ReasoningEngine.user_positions["SAIL"] = {
        "qty": 100,
        "entry_price": 95.0,
        "stoploss": 90.0,
    }

    res = client.get("/api/risk/exposure")
    json_data = res.json()
    assert json_data["status"] == "success"

    clusters = json_data["data"]["clusters"]
    assert len(clusters) == 1

    cluster_entry = clusters[0]
    assert cluster_entry["cluster"] == "PSU_METALS_INFRA"
    assert cluster_entry["open_risk"] == 500.0
    assert "limit" in cluster_entry
    assert "pct_of_limit" in cluster_entry

    # Check the limit calculation: capital * max_cluster_risk_pct / 100
    capital = json_data["data"]["capital"]
    max_pct = json_data["data"]["max_cluster_risk_pct"]
    expected_limit = capital * max_pct / 100.0
    assert cluster_entry["limit"] == expected_limit

    # Check pct_of_limit
    expected_pct = 500.0 / expected_limit
    assert abs(cluster_entry["pct_of_limit"] - expected_pct) < 0.0001


def test_multiple_positions_same_cluster():
    """Multiple positions in the same cluster should aggregate."""
    # ADANIENT and ADANIGREEN both map to ADANI cluster
    ReasoningEngine.user_positions["ADANIENT"] = {
        "qty": 100,
        "entry_price": 100.0,
        "stoploss": 95.0,
    }
    ReasoningEngine.user_positions["ADANIGREEN"] = {
        "qty": 50,
        "entry_price": 200.0,
        "stoploss": 190.0,
    }

    res = client.get("/api/risk/exposure")
    json_data = res.json()

    clusters = json_data["data"]["clusters"]
    # Should have only ADANI cluster (both positions map to it)
    assert len(clusters) == 1
    assert clusters[0]["cluster"] == "ADANI"

    # Risk: 100*5 + 50*10 = 500 + 500 = 1000
    assert clusters[0]["open_risk"] == 1000.0


def test_multiple_different_clusters():
    """Positions in different clusters should each appear as separate rows."""
    ReasoningEngine.user_positions["SAIL"] = {
        "qty": 100,
        "entry_price": 95.0,
        "stoploss": 90.0,
    }
    ReasoningEngine.user_positions["INFY"] = {
        "qty": 50,
        "entry_price": 3000.0,
        "stoploss": 2950.0,
    }

    res = client.get("/api/risk/exposure")
    json_data = res.json()

    clusters = json_data["data"]["clusters"]
    assert len(clusters) == 2

    cluster_ids = {c["cluster"] for c in clusters}
    assert "PSU_METALS_INFRA" in cluster_ids
    assert "IT" in cluster_ids


def test_sort_by_pct_of_limit_descending():
    """Clusters should be sorted by pct_of_limit descending (highest % first)."""
    # SAIL: 500 risk (PSU_METALS_INFRA cluster)
    ReasoningEngine.user_positions["SAIL"] = {
        "qty": 100,
        "entry_price": 95.0,
        "stoploss": 90.0,
    }
    # INFY: 2500 risk (IT cluster) - much larger
    ReasoningEngine.user_positions["INFY"] = {
        "qty": 50,
        "entry_price": 3000.0,
        "stoploss": 2950.0,
    }

    res = client.get("/api/risk/exposure")
    json_data = res.json()

    clusters = json_data["data"]["clusters"]

    # IT (INFY) should have a higher pct_of_limit and appear first
    assert clusters[0]["cluster"] == "IT"
    assert clusters[1]["cluster"] == "PSU_METALS_INFRA"
    assert clusters[0]["pct_of_limit"] > clusters[1]["pct_of_limit"]


def test_pct_of_limit_can_exceed_one():
    """pct_of_limit can legitimately exceed 1.0 if over-leveraged."""
    # Fetch once to read the actual capital and limit from config
    res = client.get("/api/risk/exposure")
    json_data = res.json()
    capital = json_data["data"]["capital"]
    max_cluster_risk_pct = json_data["data"]["max_cluster_risk_pct"]
    limit = capital * max_cluster_risk_pct / 100.0

    # Create a position that uses 150% of the cluster limit
    # Entry: 95, Stop: 90, risk per share = 5
    # Need qty such that qty * 5 = 1.5 * limit
    qty_needed = int(1.5 * limit / 5.0) + 1  # +1 to ensure we exceed 1.0
    ReasoningEngine.user_positions["SAIL"] = {
        "qty": qty_needed,
        "entry_price": 95.0,
        "stoploss": 90.0,
    }

    res2 = client.get("/api/risk/exposure")
    json_data2 = res2.json()

    clusters = json_data2["data"]["clusters"]
    assert len(clusters) == 1
    # pct_of_limit should be > 1.0 (150% of limit)
    assert clusters[0]["pct_of_limit"] > 1.0


def test_positions_without_qty_or_stop_are_ignored():
    """Positions missing qty or stoploss should not contribute to risk."""
    ReasoningEngine.user_positions["SAIL"] = {
        "entry_price": 95.0,
        "stoploss": 90.0,
    }
    ReasoningEngine.user_positions["INFY"] = {
        "qty": 100,
        "entry_price": 3000.0,
    }

    res = client.get("/api/risk/exposure")
    json_data = res.json()

    # Only INFY should appear since SAIL has no qty and INFY has no stoploss
    clusters = json_data["data"]["clusters"]
    assert len(clusters) == 0


def test_response_schema_contains_required_fields():
    """The response should match the spec schema."""
    ReasoningEngine.user_positions["SAIL"] = {
        "qty": 100,
        "entry_price": 95.0,
        "stoploss": 90.0,
    }

    res = client.get("/api/risk/exposure")
    json_data = res.json()

    # Top level
    assert json_data["status"] == "success"
    assert "data" in json_data

    # Data level
    data = json_data["data"]
    assert "capital" in data
    assert "max_cluster_risk_pct" in data
    assert "clusters" in data
    assert isinstance(data["clusters"], list)

    # Cluster level (if present)
    if data["clusters"]:
        cluster = data["clusters"][0]
        assert "cluster" in cluster
        assert "open_risk" in cluster
        assert "limit" in cluster
        assert "pct_of_limit" in cluster
