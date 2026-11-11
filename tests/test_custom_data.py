"""Tests for SellerContext isolation, CSV/JSON upload validation, safety, and graceful degradation."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.data.upload import DatasetValidationError, build_template_payload, parse_uploaded_csv
from app.engine import diagnosis
from app.llm.mock_provider import MockLLMProvider
from app.llm.router import LLMRouter
from app.main import create_app


def _make_app():
    settings = Settings(
        groq_api_key="",
        gemini_api_key="",
        openrouter_api_key="",
        allow_mock_llm=True,
        max_upload_skus=2000,
        max_upload_bytes=1_048_576,
    )
    router = LLMRouter(providers=[MockLLMProvider()])
    return create_app(settings_override=settings, router_override=router, skip_startup_probes=True)


def test_template_download_and_roundtrip_csv_and_json():
    client = TestClient(_make_app())

    for fmt in ("csv", "cancellations_csv", "json"):
        r = client.get(f"/api/data/template?format={fmt}")
        assert r.status_code == 200
        assert "attachment; filename=" in r.headers.get("content-disposition", "")

    csv_text, _, _ = build_template_payload("csv")
    cancel_text, _, _ = build_template_payload("cancellations_csv")
    val_resp = client.post(
        "/api/data/validate",
        files={
            "file": ("skus.csv", csv_text.encode("utf-8"), "text/csv"),
            "cancellations_file": ("cancels.csv", cancel_text.encode("utf-8"), "text/csv"),
        },
        data={"shop_name": "Template Check Store", "seller_name": "Asha Patel"},
    )
    assert val_resp.status_code == 200
    body = val_resp.json()
    assert body["valid"] is True
    assert body["dataset_id"].startswith("ds_")
    assert body["summary"]["sku_count"] == 3
    assert body["summary"]["seller"]["shop_name"] == "Template Check Store"

    # Attach validated dataset_id to session & demo-data
    demo = client.get(f"/api/demo-data?dataset_id={body['dataset_id']}")
    assert demo.status_code == 200
    assert demo.json()["overview"]["seller"]["shop_name"] == "Template Check Store"

    start = client.post(
        "/api/session/start",
        json={"mode": "inbound", "language": "en-IN", "dataset_id": body["dataset_id"]},
    )
    assert start.status_code == 200
    assert start.json()["session"]["dataset"]["sku_count"] == 3

    # JSON template roundtrip
    json_text, _, _ = build_template_payload("json")
    val_json = client.post(
        "/api/data/validate",
        content=json_text.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert val_json.status_code == 200
    assert val_json.json()["valid"] is True


def test_missing_optional_columns_degrade_gracefully():
    minimal_csv = (
        "sku_id,name,price,orders_prev,orders_curr\n"
        "MIN-1,Cotton Towel Set,400,50,30\n"
        "MIN-2,Stainless Bowl Pack,600,40,35\n"
    ).encode("utf-8")

    ctx = parse_uploaded_csv(minimal_csv)
    diag = diagnosis.run_diagnosis(ctx, "current")
    oos = diagnosis.get_out_of_stock_skus(ctx, snapshot="current")
    pricing = diagnosis.get_pricing_gaps(ctx, snapshot="current")
    cancels = diagnosis.get_cancellation_breakdown(ctx, snapshot="current")
    funnel = diagnosis.get_visibility_funnel(ctx, snapshot="current")

    assert oos["assessable"] is False
    assert oos["status"] == "not assessable with the data provided"
    assert pricing["assessable"] is False
    assert pricing["status"] == "not assessable with the data provided"
    assert cancels["assessable"] is False
    assert cancels["status"] == "not assessable with the data provided"
    assert funnel["assessable"] is False
    assert funnel["status"] == "not assessable with the data provided"

    assert diag["total_lost_orders"] == 25
    assert diag["unexplained_residual"]["lost_orders"] == 25
    assert len(diag["missing_signals"]) == 4
    by_driver = {d["driver"]: d for d in diag["drivers"]}
    assert by_driver["stock_outs"]["assessable"] is False
    assert "not assessable with the data provided" in by_driver["stock_outs"]["evidence"]
    assert by_driver["pricing_gaps"]["assessable"] is False
    assert "not assessable with the data provided" in by_driver["pricing_gaps"]["evidence"]


def test_formula_injection_and_html_sanitization_and_caps():
    for prefix in ("=CMD|'/C calc'!A0", "+1+1", "-2+3", "@SUM(A1:A2)"):
        bad_csv = (
            "sku_id,name,price,orders_prev,orders_curr\n"
            f"SKU-1,{prefix},250,10,5\n"
        ).encode("utf-8")
        try:
            parse_uploaded_csv(bad_csv)
            raise AssertionError(f"Expected formula rejection for prefix {prefix!r}")
        except DatasetValidationError as exc:
            assert any("formula safety" in e.lower() for e in exc.errors)

    # HTML stripping inside name & category
    html_csv = (
        "sku_id,name,category,price,orders_prev,orders_curr\n"
        'SKU-H1,"<script>alert(1)</script>Steel <b>Kettle</b>","<style>x{}</style>Home",500,20,10\n'
    ).encode("utf-8")
    ctx_html = parse_uploaded_csv(html_csv)
    assert ctx_html.skus[0].name == "Steel Kettle"
    assert ctx_html.skus[0].category == "Home"

    # Row cap check
    tiny_cfg = Settings(max_upload_skus=3, max_upload_bytes=1_048_576, allow_mock_llm=True)
    many_rows = "sku_id,name,price,orders_prev,orders_curr\n" + "\n".join(
        f"S-{i},Item {i},100,10,8" for i in range(4)
    )
    try:
        parse_uploaded_csv(many_rows.encode("utf-8"), settings=tiny_cfg)
        raise AssertionError("Expected row cap error")
    except DatasetValidationError as exc:
        assert any("maximum allowed is 3" in e.lower() for e in exc.errors)

    # Size cap check
    size_cfg = Settings(max_upload_skus=2000, max_upload_bytes=64, allow_mock_llm=True)
    try:
        parse_uploaded_csv(many_rows.encode("utf-8"), settings=size_cfg)
        raise AssertionError("Expected size limit error")
    except DatasetValidationError as exc:
        assert any("size limit" in e.lower() for e in exc.errors)

    # Bad clicks > impressions, duplicate IDs, zero price via CSV HTTP 422, plus negative price via JSON
    client = TestClient(_make_app())
    broken_csv = (
        "sku_id,name,price,orders_prev,orders_curr,impressions_prev,clicks_prev\n"
        "DUP-1,Item A,0,20,10,100,150\n"
        "DUP-1,Item B,200,10,5,100,20\n"
    ).encode("utf-8")
    resp = client.post(
        "/api/data/validate",
        files={"file": ("broken.csv", broken_csv, "text/csv")},
    )
    assert resp.status_code == 422
    errs = resp.json()["errors"]
    assert any("duplicate sku_id" in e.lower() for e in errs)
    assert any("greater than 0" in e.lower() for e in errs)
    assert any("clicks_prev" in e.lower() for e in errs)

    neg_json = json.dumps(
        {"skus": [{"sku_id": "N-1", "name": "Bad Price", "price": -50, "orders_prev": 10, "orders_curr": 5}]}
    ).encode("utf-8")
    resp_neg = client.post(
        "/api/data/validate",
        content=neg_json,
        headers={"Content-Type": "application/json"},
    )
    assert resp_neg.status_code == 422
    assert any("greater than 0" in e.lower() for e in resp_neg.json()["errors"])


def test_session_isolation_across_concurrent_uploads_and_minimal_chat():
    client = TestClient(_make_app())
    csv_a = (
        "sku_id,name,price,orders_prev,orders_curr,days_out_of_stock_curr\n"
        "A-1,SENTINEL_ALPHA_9918,1000,50,20,18\n"
    ).encode("utf-8")
    csv_b = json.dumps(
        {
            "seller": {"shop_name": "Beta Boutique", "seller_name": "Meera Nair", "salutation": "Meera ji"},
            "skus": [
                {"sku_id": "B-1", "name": "Silk Scarf", "price": 750, "orders_prev": 80, "orders_curr": 50}
            ],
        }
    ).encode("utf-8")

    s_a = client.post(
        "/api/session/start",
        files={"file": ("a.csv", csv_a, "text/csv")},
        data={"mode": "proactive", "language": "en-IN", "shop_name": "Alpha Store", "seller_name": "Kabir"},
    )
    assert s_a.status_code == 200
    sid_a = s_a.json()["session_id"]

    s_b = client.post(
        "/api/session/start",
        files={"file": ("b.json", csv_b, "application/json")},
        data={"mode": "inbound", "language": "en-IN"},
    )
    assert s_b.status_code == 200
    sid_b = s_b.json()["session_id"]

    # Sessions remain isolated with independent SellerContexts
    assert client.get(f"/api/session/{sid_a}").json()["dataset"]["seller"]["shop_name"] == "Alpha Store"
    assert client.get(f"/api/session/{sid_b}").json()["dataset"]["seller"]["shop_name"] == "Beta Boutique"

    # Minimal upload session (no optional columns) runs cleanly and states missing signals are not assessable
    g_b = client.post(f"/api/session/{sid_b}/greeting").text
    assert "Meera" in g_b
    _ = client.post("/api/chat", json={"session_id": sid_b, "text": "Sales fell this month."}).text
    _ = client.post("/api/chat", json={"session_id": sid_b, "text": "Show me my data and tell me why."}).text
    diag_state = client.get(f"/api/session/{sid_b}").json()
    if diag_state["phase"] != "DIAGNOSE":
        diag_turn = client.post("/api/chat", json={"session_id": sid_b, "text": "Check my data and show me now."}).text
        assert "not assessable with the data provided" in diag_turn.lower()

    # Check RAM-only guarantee: sentinel SKU name never written anywhere in repo workspace
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent
    for log_candidate in repo_root.rglob("*.log"):
        assert "SENTINEL_ALPHA_9918" not in log_candidate.read_text(encoding="utf-8", errors="ignore")
