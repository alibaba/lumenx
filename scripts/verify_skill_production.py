"""Production-profile readiness gate for the local/cloud deployment contract."""

from __future__ import annotations

import argparse
import json
import os
import socket
from urllib import request


def get_json(url: str) -> dict:
    with request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy", default="http://127.0.0.1:17178")
    parser.add_argument("--worker-metrics", default="http://127.0.0.1:19100/metrics")
    parser.add_argument("--prometheus", default="http://127.0.0.1:19090")
    parser.add_argument("--otel", default="http://127.0.0.1:14318")
    parser.add_argument("--allow-local", action="store_true")
    args = parser.parse_args()
    if not args.allow_local:
        for name in ("LUMENX_CREDENTIAL_PROXY_TOKEN", "LUMENX_CREDENTIAL_CORE_TOKEN"):
            if os.getenv(name, "").startswith("local-") or not os.getenv(name):
                parser.error(f"{name} must be set to a non-local production secret")
        if os.getenv("LUMENX_CREDENTIAL_PROXY_PROVIDER") != "file":
            parser.error("LUMENX_CREDENTIAL_PROXY_PROVIDER must be file in production")
        if not os.path.isfile(os.getenv("LUMENX_CREDENTIAL_PROXY_SECRET_FILE", "")):
            parser.error("LUMENX_CREDENTIAL_PROXY_SECRET_FILE must point to a mounted secret file")
    if get_json(f"{args.proxy}/healthz").get("status") != "ok":
        return 1
    if get_json(f"{args.proxy}/readyz").get("status") != "ready":
        return 1
    metrics = request.urlopen(args.worker_metrics, timeout=5).read().decode()
    if "lumenx_skill_worker_claimed_total" not in metrics:
        return 1
    targets = get_json(f"{args.prometheus}/api/v1/targets")["data"]["activeTargets"]
    if not any(target.get("health") == "up" for target in targets):
        return 1
    otel_host = args.otel.split("://", 1)[-1].split(":", 1)
    if len(otel_host) != 2:
        return 1
    with socket.create_connection((otel_host[0], int(otel_host[1].split("/", 1)[0])), timeout=5):
        pass
    print(json.dumps({"proxy": "ready", "worker_metrics": "up", "prometheus_target": "up", "otel": "reachable"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
