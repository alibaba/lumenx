"""Explicitly run a reversible Kubernetes Worker restart drill."""

from __future__ import annotations

import argparse
import subprocess


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default="lumenx")
    parser.add_argument("--execute", action="store_true", help="required to mutate the deployment")
    args = parser.parse_args()
    if not args.execute:
        print("dry-run: would scale lumenx-skill-worker to 0, then 2 and wait for rollout")
        return 0
    run("kubectl", "scale", "deployment/lumenx-skill-worker", "--replicas=0", "--namespace", args.namespace)
    run("kubectl", "wait", "--for=delete", "pod", "-l", "app=lumenx-skill-worker", "--namespace", args.namespace, "--timeout=2m")
    run("kubectl", "scale", "deployment/lumenx-skill-worker", "--replicas=2", "--namespace", args.namespace)
    run("kubectl", "rollout", "status", "deployment/lumenx-skill-worker", "--namespace", args.namespace, "--timeout=5m")
    run("kubectl", "get", "pods", "-l", "app=lumenx-skill-worker", "--namespace", args.namespace)
    print("kubernetes worker restart drill: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
