"""Identify a single same-major patch that fixes an OSV advisory."""
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request


def pins(content):
    packages = {}
    for line in content.splitlines():
        if not line or line.startswith("#"):
            continue
        name, version = line.split("==")
        if name in packages or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", version):
            raise ValueError(f"Unexpected pin for {name}")
        packages[name] = tuple(int(part) for part in version.split("."))
    return packages


def advisories(queries):
    request = urllib.request.Request(
        "https://api.osv.dev/v1/querybatch",
        json.dumps({"queries": queries}).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        results = json.load(response)["results"]
    if len(results) != len(queries):
        raise RuntimeError("Incomplete OSV response")
    return [entry.get("vulns", []) for entry in results]


def query(name, version):
    return {"package": {"name": name, "ecosystem": "PyPI"},
            "version": ".".join(map(str, version))}


def eligible(base, candidate):
    changed = [name for name in base if base[name] != candidate.get(name)]
    if base.keys() != candidate.keys() or len(changed) != 1:
        return False
    name, = changed
    old, new = base[name], candidate[name]
    if old[0] != new[0] or new <= old:
        return False
    before, after = advisories([query(name, old), query(name, new)])
    return bool(before) and not after


if __name__ == "__main__":
    old_pins = pins(subprocess.check_output(
        ["git", "show", f"{os.environ['BASE_SHA']}:requirements.txt"], text=True,
    ))
    new_pins = pins(Path("requirements.txt").read_text())
    matches = advisories([query(name, version) for name, version in new_pins.items()])
    for name, issues in zip(new_pins, matches):
        for issue in issues:
            print(name, issue["id"])
    if any(matches):
        raise SystemExit("Proposed dependencies still contain known vulnerabilities")
    can_merge = eligible(old_pins, new_pins)
    print(f"Eligible for automatic update: {can_merge}")
    if os.getenv("REQUIRE_ELIGIBLE") == "1" and not can_merge:
        raise SystemExit("This update requires review")
