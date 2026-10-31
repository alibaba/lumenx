"""Static safety gate for the Skill runtime Kubernetes template."""

from __future__ import annotations

from pathlib import Path
import sys
import argparse

import yaml


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--image-prefix", default="")
    parser.add_argument("--image-tag", default="")
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / "deploy/k8s/skill-runtime.yaml"
    documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    deployments = {doc["metadata"]["name"]: doc for doc in documents if doc.get("kind") == "Deployment"}
    for name in ("lumenx-skill-worker", "lumenx-credential-proxy"):
        deployment = deployments[name]
        if deployment["spec"]["replicas"] < 2:
            raise SystemExit(f"{name} requires at least two replicas")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        security = container["securityContext"]
        if not security.get("readOnlyRootFilesystem") or security.get("allowPrivilegeEscalation"):
            raise SystemExit(f"{name} has unsafe container security context")
        if "ALL" not in security.get("capabilities", {}).get("drop", []):
            raise SystemExit(f"{name} must drop all Linux capabilities")
        if any("hostPort" in port for port in container.get("ports", [])):
            raise SystemExit(f"{name} must not publish host ports")
        image = container.get("image", "")
        if args.production:
            if not args.image_prefix or not args.image_tag:
                raise SystemExit("production validation requires --image-prefix and --image-tag")
            expected = f"{args.image_prefix}/lumenx-{('skill-worker' if name == 'lumenx-skill-worker' else 'credential-proxy')}:{args.image_tag}"
            if image != expected:
                raise SystemExit(f"{name} image must be {expected}, got {image}")
    proxy = deployments["lumenx-credential-proxy"]
    if not any(volume.get("secret", {}).get("secretName") == "lumenx-provider-credentials" for volume in proxy["spec"]["template"]["spec"].get("volumes", [])):
        raise SystemExit("credential proxy must mount the provider credential Secret")
    print("skill runtime Kubernetes template: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
