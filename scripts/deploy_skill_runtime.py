"""Build, publish and roll out the Skill runtime in a real Kubernetes cluster.

The command refuses to apply without a selected kubectl context, registry
coordinates, non-local credentials and an externally provisioned Secret.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "deploy/k8s/skill-runtime.yaml"


def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(args, cwd=ROOT, check=True, text=True, capture_output=capture)
    return result.stdout if capture else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True, help="registry prefix, e.g. ghcr.io/acme")
    parser.add_argument("--tag", required=True, help="immutable tag or digest suffix")
    parser.add_argument("--namespace", default="lumenx")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--skip-push", action="store_true")
    args = parser.parse_args()
    if args.tag == "latest" or "REPLACE" in args.registry or "REPLACE" in args.tag:
        parser.error("production rollout requires an immutable non-placeholder image tag")
    context = run("kubectl", "config", "current-context", capture=True).strip()
    if not context:
        parser.error("kubectl current-context is required")
    if args.apply and os.getenv("LUMENX_K8S_EXTERNAL_SECRETS") != "1":
        parser.error("set LUMENX_K8S_EXTERNAL_SECRETS=1 after provisioning KMS/Secret CSI resources")
    worker_image = f"{args.registry}/lumenx-skill-worker:{args.tag}"
    proxy_image = f"{args.registry}/lumenx-credential-proxy:{args.tag}"
    run("docker", "build", "-f", "Dockerfile.skill-worker", "-t", worker_image, ".")
    run("docker", "build", "-f", "Dockerfile.credential-proxy", "-t", proxy_image, ".")
    if not args.skip_push:
        run("docker", "push", worker_image)
        run("docker", "push", proxy_image)
    rendered = MANIFEST.read_text(encoding="utf-8").replace(
        "ghcr.io/REPLACE/lumenx-skill-worker:REPLACE_TAG", worker_image
    ).replace("ghcr.io/REPLACE/lumenx-credential-proxy:REPLACE_TAG", proxy_image)
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as output:
        output.write(rendered)
        rendered_path = output.name
    try:
        if not args.apply:
            run("kubectl", "apply", "--dry-run=client", "--namespace", args.namespace, "-f", rendered_path)
            print(f"rendered and client-validated for context={context}: {rendered_path}")
            return 0
        run("kubectl", "apply", "--namespace", args.namespace, "--server-side", "-f", rendered_path)
        run("kubectl", "rollout", "status", "deployment/lumenx-skill-worker", "--namespace", args.namespace, "--timeout=5m")
        run("kubectl", "rollout", "status", "deployment/lumenx-credential-proxy", "--namespace", args.namespace, "--timeout=5m")
        print(f"rollout complete for context={context}, namespace={args.namespace}")
        return 0
    finally:
        Path(rendered_path).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
