"""Small tracked-file hygiene scan. Complements manual review; not a full secret scanner."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = [
    re.compile(rb"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]
FORBIDDEN = {".venv", ".terraform", "__pycache__", "build", "dist", ".tools", ".aws"}


def main():
    files = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    findings = []
    checked = 0
    for name in filter(None, files):
        path = Path(name)
        if (
            FORBIDDEN.intersection(path.parts)
            or ".tfstate" in name
            or path.suffix in {".tfplan", ".tfvars", ".pem", ".key"}
            or path.name.startswith(".env")
            or path.name == "credentials"
        ):
            findings.append(f"forbidden artifact: {name}")
        raw = (ROOT / path).read_bytes()
        if any(pattern.search(raw) for pattern in SECRET_PATTERNS):
            findings.append(f"possible secret: {name}")
        checked += 1
    if findings:
        raise SystemExit("\n".join(findings))
    print(
        f"Hygiene scan passed: {checked} tracked files; no matched secrets or forbidden artifacts."
    )


if __name__ == "__main__":
    main()
