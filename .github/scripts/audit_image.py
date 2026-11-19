"""Inventory the deployed Modal image in an isolated sandbox and query OSV."""
import argparse
import json
import re
import subprocess
import urllib.request

import modal

from security_update import advisories


INVENTORY = r"""
from importlib.metadata import distributions
import json
from pathlib import Path
import platform
import subprocess

os_info = dict(
    line.split("=", 1)
    for line in Path("/etc/os-release").read_text().splitlines()
    if "=" in line
)
assert os_info["ID"] == "debian", "Unsupported base image distribution"
print(json.dumps({
    "debian": os_info["VERSION_ID"].strip('"'),
    "python": platform.python_version(),
    "python_packages": [
        {"name": package.metadata["Name"], "version": package.version}
        for package in distributions()
    ],
    "deb": subprocess.check_output(
        ["dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${source:Package}\t${source:Version}\n"],
        text=True,
    ).splitlines(),
}))
"""


def deployed_image_id():
    info = json.loads(subprocess.check_output(
        ["modal", "function", "info", "streaming-dictation/StreamingDictation.*",
         "--env", "main", "--json"],
        text=True,
    ))
    return info["image_info"]["image_id"]


def inventory(image_id):
    print(f"Auditing deployed image {image_id}", flush=True)
    app = modal.App.lookup("streaming-dictation", environment_name="main")
    sandbox = modal.Sandbox.create(
        "python", "-c", INVENTORY, app=app, image=modal.Image.from_id(image_id),
        block_network=True, timeout=120,
    )
    try:
        output = sandbox.stdout.read()
        sandbox.wait()
        if sandbox.returncode != 0:
            raise RuntimeError(sandbox.stderr.read())
        return json.loads(output)
    finally:
        sandbox.terminate()


def query(ecosystem, name, version):
    return {"package": {"ecosystem": ecosystem, "name": name}, "version": version}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="Audit a candidate Modal image ID before deployment")
    args = parser.parse_args()
    data = inventory(args.image or deployed_image_id())
    debian = f"Debian:{data['debian']}"
    packages = set()
    for line in data["deb"]:
        status, name, version = line.split("\t")
        if status.startswith("ii"):
            packages.add((debian, name, version))
    packages.update(("PyPI", p["name"], p["version"]) for p in data["python_packages"])
    print(f"Checking {len(packages)} Python and Debian package versions")
    queries = [query(*package) for package in sorted(packages)]
    findings = []
    for start in range(0, len(queries), 75):
        batch = queries[start:start + 75]
        for package, issues in zip(batch, advisories(batch)):
            if issues:
                findings.append((package, issues))
    for package, issues in findings:
        ids = ", ".join(issue["id"] for issue in issues)
        print(f"{package['package']['name']} {package['version']} ({package['package']['ecosystem']}) {ids}")

    python = tuple(map(int, data["python"].split(".")))
    with urllib.request.urlopen("https://www.python.org/ftp/python/", timeout=30) as response:
        index = response.read().decode()
    available = [
        tuple(map(int, release.split(".")))
        for release in re.findall(r'href="(\d+\.\d+\.\d+)/"', index)
        if release.startswith(f"{python[0]}.{python[1]}.")
    ]
    latest = max(available)
    print(f"CPython {data['python']}, latest in this series {'.'.join(map(str, latest))}")
    if findings or python < latest:
        raise SystemExit("Deployed image needs security review")


if __name__ == "__main__":
    main()
