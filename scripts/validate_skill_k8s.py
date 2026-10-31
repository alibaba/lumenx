"""Static safety gate for the Skill runtime Kubernetes template."""

from __future__ import annotations

from pathlib import Path
import sys

import yaml


def main() -> int:
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
    proxy = deployments["lumenx-credential-proxy"]
    if not any(volume.get("secret", {}).get("secretName") == "lumenx-provider-credentials" for volume in proxy["spec"]["template"]["spec"].get("volumes", [])):
        raise SystemExit("credential proxy must mount the provider credential Secret")
    print("skill runtime Kubernetes template: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
