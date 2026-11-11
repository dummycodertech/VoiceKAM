"""End-to-end HTTP + SSE + TTS smoke verification script (local, Cloudflare Tunnel, or Render).

Usage:
    python scripts/smoke_test.py
    python scripts/smoke_test.py --url https://voicekam-virtual-kam.onrender.com
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        ev_name = "message"
        data_obj: dict = {}
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev_name = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    data_obj = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    pass
        events.append((ev_name, data_obj))
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test VoiceKAM deployment.")
    parser.add_argument("--url", default="http://127.0.0.1:7860", help="Base URL of running service")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout (higher for Render cold start)")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    print(f"[1/5] Checking {base}/api/health ...")
    t0 = time.perf_counter()
    with httpx.Client(timeout=args.timeout) as client:
        r = client.get(f"{base}/api/health")
        r.raise_for_status()
        health = r.json()
        print(f"  OK ({round((time.perf_counter() - t0) * 1000)} ms) | chain={health.get('active_llm_chain')}")
        for entry in health.get("llm_startup_smoke") or []:
            print(f"  LLM Smoke [{entry.get('provider')}:{entry.get('model')}]: ok={entry.get('smoke_ok')} tool={entry.get('tool_called')} latency={entry.get('latency_ms')}ms err={entry.get('error')}")

        print(f"[2/5] Checking {base}/api/tts-probe ...")
        rp = client.get(f"{base}/api/tts-probe")
        rp.raise_for_status()
        probe = rp.json()
        print(f"  edge_tts_ok={probe.get('edge_tts_ok')} | recommended={probe.get('recommended_tts_mode')} | latency={probe.get('latency_ms')}ms | bytes={probe.get('bytes')}")
        if not probe.get("edge_tts_ok"):
            print(f"  edge-tts probe error: {probe.get('error_type')}: {probe.get('error')}")

        print(f"[3/5] Checking {base}/api/tts ...")
        rt = client.post(f"{base}/api/tts", json={"text": "Namaste, I am Metis, checking your sales data.", "language": "en-IN"})
        rt.raise_for_status()
        ct = rt.headers.get("content-type", "")
        print(f"  TTS Content-Type={ct} | X-TTS-Provider={rt.headers.get('x-tts-provider', 'json-fallback')} | size={len(rt.content)} bytes")

        print(f"[4/5] Checking {base}/api/scenarios, template & demo-data ...")
        rsc = client.get(f"{base}/api/scenarios")
        rsc.raise_for_status()
        scen_list = rsc.json().get("scenarios") or []
        assert len(scen_list) >= 4, f"Expected >= 4 scenarios, got {len(scen_list)}"

        rtpl = client.get(f"{base}/api/data/template?format=csv")
        rtpl.raise_for_status()
        assert "sku_id" in rtpl.text and "orders_curr" in rtpl.text

        rd = client.get(f"{base}/api/demo-data")
        rd.raise_for_status()
        demo = rd.json()
        metrics = demo["metrics"]["metrics"]
        diag = demo["diagnosis"]
        assert metrics["sales_gmv"]["baseline"] > 0
        assert sum(d["lost_orders"] for d in diag["drivers"]) == diag["total_lost_orders"]
        assert sum(d["lost_revenue"] for d in diag["drivers"]) == diag["total_lost_revenue"]
        print(
            f"  Demo data reconciled ({len(scen_list)} scenarios; orders {metrics['orders']['baseline']} -> {metrics['orders']['current']}, "
            f"GMV Rs {metrics['sales_gmv']['baseline']:,} -> Rs {metrics['sales_gmv']['current']:,})"
        )

        print(f"[5/5] Testing full /api/session/start + SSE /api/chat turn ...")
        rs = client.post(f"{base}/api/session/start", json={"mode": "inbound", "language": "en-IN"})
        rs.raise_for_status()
        sid = rs.json()["session_id"]

        rg = client.post(f"{base}/api/session/{sid}/greeting")
        rg.raise_for_status()
        gevs = _parse_sse(rg.text)
        gdone = next((d for e, d in gevs if e == "done"), None)
        assert gdone is not None, "Greeting did not emit done event"
        print(f"  Greeting: {gdone.get('text')}")

        rc = client.post(
            f"{base}/api/chat",
            json={"session_id": sid, "text": "My sales dropped significantly this month. Please check my data and tell me why."},
        )
        rc.raise_for_status()
        cevs = _parse_sse(rc.text)
        err = next((d for e, d in cevs if e == "error"), None)
        if err:
            print(f"  ERROR SSE: {err}")
            return 1
        cdone = next((d for e, d in cevs if e == "done"), None)
        clat = next((d for e, d in cevs if e == "latency"), None)
        assert cdone is not None, "Chat turn did not emit done event"
        print(f"  Turn 1 [{cdone.get('phase')}] ({cdone.get('provider')}, {clat.get('total_turn_ms') if clat else '?'}ms): {cdone.get('text')}")

    print("ALL SMOKE CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

