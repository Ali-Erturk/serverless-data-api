# Development

Python 3.12 is used locally and in Lambda/CI. `requirements.in` holds runtime dependency ranges;
`requirements-dev.in` adds test/development tools. Hashed lock files pin resolved versions.
Install them with pip's `--require-hashes` and install the local package with `--no-deps`.

Lock updates should be intentional: regenerate, inspect the diff, install into a clean environment,
run Python and Terraform checks and confirm the GitHub Actions Ubuntu run succeeds. Linux and
Windows must both be represented; do not publish a lock that unconditionally requires Windows-only
packages. The lock-generation command used is recorded in the file headers.

```powershell
uv pip compile --universal --python-version 3.12 --generate-hashes --no-emit-index-url requirements.in -o requirements.lock
uv pip compile --universal --python-version 3.12 --generate-hashes --no-emit-index-url requirements-dev.in -o requirements-dev.lock
```

The Lambda builder targets `manylinux2014_x86_64` Python 3.12 wheels even on Windows. It creates a
temporary staging directory under ignored `build/`, copies application source without bytecode,
and writes a ZIP with deterministic member order/timestamps. `build/lambda.zip` is not committed;
build it before Terraform plan/test/validate. Terraform checks its hash for updates.

CI has read-only repository permissions and no AWS credentials. `terraform init -backend=false`
downloads only providers. `terraform test` uses `mock_provider "aws"` with plan assertions, not
real AWS. Provider checksums for Linux and Windows are committed in `infra/.terraform.lock.hcl`.

Useful checks:

```powershell
ruff check .
ruff format --check .
pytest --cov=data_api --cov-report=term-missing --cov-fail-under=85
python scripts/build_lambda.py
terraform -chdir=infra fmt -check
terraform -chdir=infra validate
terraform -chdir=infra test
```

To change the data contract, update constants, parser checks, boundary tests, README limits and
affected Terraform timeouts/lifecycle settings together. Caller input is never treated as an object
key, log message or expression fragment.
