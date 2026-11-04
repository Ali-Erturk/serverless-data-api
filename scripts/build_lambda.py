"""Build a Linux Python 3.12 ZIP with pinned dependencies, from any host OS."""

import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lambda-", dir=build) as temporary:
        package = Path(temporary)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "--no-compile",
                "--platform",
                "manylinux2014_x86_64",
                "--implementation",
                "cp",
                "--python-version",
                "3.12",
                "--only-binary=:all:",
                "--target",
                str(package),
                "-r",
                str(ROOT / "requirements.lock"),
            ],
            check=True,
        )
        shutil.copytree(
            ROOT / "src" / "data_api",
            package / "data_api",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        destination = build / "lambda.zip"
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(package.rglob("*")):
                if file.is_file() and "__pycache__" not in file.parts:
                    info = zipfile.ZipInfo(
                        file.relative_to(package).as_posix(), (2026, 1, 1, 0, 0, 0)
                    )
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    archive.writestr(info, file.read_bytes())
    print(
        f"Built {destination.name}: sha256={hashlib.sha256(destination.read_bytes()).hexdigest()}"
    )


if __name__ == "__main__":
    main()
