from __future__ import annotations

import respx
from fastapi.testclient import TestClient
from httpx import Response

from app import settings_store
from app.metrics import delta_hit_rate, parse_metrics, summarize

# Old-style prefix (sglang:) — what most deployed versions emit.
COLON = """# HELP sglang:cache_hit_rate Cache hit rate
# TYPE sglang:cache_hit_rate gauge
sglang:cache_hit_rate{model_name="qwen"} 0.87
sglang:hicache_host_used_tokens{model_name="qwen"} 250000
sglang:hicache_host_total_tokens{model_name="qwen"} 1000000
sglang:num_used_tokens 40000
sglang:max_total_num_tokens 200000
sglang:prompt_tokens_total 1000000
sglang:cached_tokens_total 800000
sglang:time_to_first_token_seconds_sum 42.0
sglang:time_to_first_token_seconds_count 10
"""

# New-style prefix (sglang_) — v0.5.4 and later.
UNDERSCORE = COLON.replace("sglang:", "sglang_")


def test_both_metric_prefixes_parse_identically():
    assert summarize(COLON) == summarize(UNDERSCORE)


def test_summary_extracts_the_hicache_gauges():
    summary = summarize(COLON)
    assert summary["hicache_enabled"] is True
    assert summary["hicache_host_used_tokens"] == 250000
    assert summary["hicache_host_utilization"] == 0.25
    assert summary["cache_hit_rate"] == 0.87
    assert summary["ttft_seconds_avg"] == 4.2


def test_missing_hicache_metrics_report_unknown_not_zero():
    summary = summarize("sglang:num_running_reqs 1\n")
    assert summary["hicache_enabled"] is False
    assert summary["hicache_host_used_tokens"] is None
    assert summary["hicache_host_utilization"] is None


def test_labels_and_comments_are_ignored():
    snapshot = parse_metrics(COLON)
    assert snapshot.value("sglang_cache_hit_rate") == 0.87
    assert all(not s.name.startswith("#") for s in snapshot.samples)


def test_delta_hit_rate_measures_only_the_window():
    before = {"prompt_tokens_total": 1000.0, "cached_tokens_total": 0.0}
    after = {"prompt_tokens_total": 3000.0, "cached_tokens_total": 1800.0}
    assert delta_hit_rate(before, after) == 0.9


def test_delta_hit_rate_is_unknown_without_traffic():
    same = {"prompt_tokens_total": 1000.0, "cached_tokens_total": 500.0}
    assert delta_hit_rate(same, same) is None


# ------------------------------------------------------------------------ API
def client(db) -> TestClient:
    from app.main import app

    return TestClient(app)


def test_collection_crud_and_budget_reporting(db):
    api = client(db)
    created = api.post("/api/collections", json={"name": "Verträge", "dpi": 150}).json()
    assert created["status"] == "pending"
    assert created["context_tokens"] == 262144
    assert created["budget_tokens"] < created["context_tokens"]

    listed = api.get("/api/collections").json()
    assert [c["id"] for c in listed] == [created["id"]]

    api.patch(f"/api/collections/{created['id']}", json={"name": "Umbenannt"})
    assert api.get(f"/api/collections/{created['id']}").json()["name"] == "Umbenannt"

    assert api.delete(f"/api/collections/{created['id']}").status_code == 204
    assert api.get("/api/collections").json() == []


def test_chat_persistence_round_trip(db):
    api = client(db)
    chat = api.post("/api/chats", json={"title": "Test", "selected_collections": ["x"]}).json()
    loaded = api.get(f"/api/chats/{chat['id']}").json()
    assert loaded["selected_collections"] == ["x"]
    assert loaded["messages"] == []

    api.patch(f"/api/chats/{chat['id']}", json={"title": "Neuer Titel"})
    assert api.get("/api/chats").json()[0]["title"] == "Neuer Titel"
    assert api.delete(f"/api/chats/{chat['id']}").status_code == 204


def test_settings_expose_schema_and_launch_command(db):
    api = client(db)
    payload = api.get("/api/settings").json()
    assert len(payload["schema"]) == len(settings_store.SPECS)
    assert "--enable-hierarchical-cache" in payload["launch_command"]
    assert "--hicache-storage-prefetch-policy wait_complete" in payload["launch_command"]
    launch_keys = {s["key"] for s in payload["schema"] if s["launch_param"]}
    assert "hicache_ratio" in launch_keys


def test_editing_the_system_prompt_reports_invalidation(db):
    api = client(db)
    api.post("/api/collections", json={"name": "A"})
    response = api.put(
        "/api/settings", json={"values": {"global_system_prompt": "Ganz anderer Prompt"}}
    ).json()
    assert response["invalidated_keys"] == ["global_system_prompt"]


def test_unknown_setting_is_rejected(db):
    api = client(db)
    assert api.put("/api/settings", json={"values": {"nope": 1}}).status_code == 400


@respx.mock
def test_health_and_metrics_proxy(db):
    settings_store.update(db, {"sglang_base_url": "http://sglang.test", "sglang_max_retries": 0})
    respx.get("http://sglang.test/health").mock(return_value=Response(200, text="ok"))
    respx.get("http://sglang.test/metrics").mock(return_value=Response(200, text=COLON))
    api = client(db)

    health = api.get("/api/sglang/health").json()
    assert health["ok"] is True

    metrics = api.get("/api/sglang/metrics").json()
    assert metrics["cache_hit_rate"] == 0.87


@respx.mock
def test_server_info_diff_reports_mismatched_launch_flags(db):
    settings_store.update(db, {"sglang_base_url": "http://sglang.test", "sglang_max_retries": 0})
    respx.get("http://sglang.test/get_server_info").mock(
        return_value=Response(
            200,
            json={
                "hicache_ratio": 4.0,  # settings say 2.0
                "hicache_write_policy": "write_through",
                "page_size": 64,
                "context_length": 262144,
            },
        )
    )
    api = client(db)
    result = api.get("/api/sglang/server-info").json()
    keys = {m["key"] for m in result["mismatches"]}
    assert keys == {"hicache_ratio"}


def test_cache_tree_labels_what_is_derived(db):
    api = client(db)
    api.post("/api/collections", json={"name": "A"})
    tree = api.get("/api/cache/tree").json()
    assert tree["provenance"]["structure"] == "abgeleitet"
    assert tree["provenance"]["hit_rate"] == "gemessen"
    assert tree["root"]["label"] == "Globaler System-Prompt"
    assert len(tree["root"]["children"]) == 1
