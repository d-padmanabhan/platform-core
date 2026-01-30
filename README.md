# platform-core

Shared platform Python library (AWS/Cloudflare/Vault utilities + core helpers).

[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-fe5196.svg?logo=conventionalcommits&logoColor=white)](https://www.conventionalcommits.org/)
![AWS](https://img.shields.io/badge/AWS-SDK-232F3E?logo=amazonaws&logoColor=white)
![Cloudflare](https://img.shields.io/badge/Cloudflare-API-F38020?logo=cloudflare&logoColor=white)
![Vault](https://img.shields.io/badge/Vault-KV%20v2-000000?logo=vault&logoColor=white)
![Kubernetes](https://img.shields.io/badge/Kubernetes-Client-326CE5?logo=kubernetes&logoColor=white)

> [!NOTE]
> This repository is currently private, but the README is written in an “open-source style”
> so it’s easy to onboard and reuse across multiple projects.

## Table of Contents

- [What this is](#what-this-is)
- [Scope and non-goals](#scope-and-non-goals)
- [Installation](#installation)
- [Quickstart](#quickstart)
- [Modules](#modules)
- [Development](#development)
- [Contributing](#contributing)
- [Security](#security)
- [License](#license)

## What this is

`platform-core` is a small Python library intended to reduce copy/paste across internal tooling by
providing:

- **AWS helpers** (client config, common patterns)
- **Cloudflare helpers** (API session, pagination, retries/backoff)
- **Vault helpers** (KV v2 read/write with timeouts)
- **Kubernetes helpers** (optional client initialization)
- **Core utilities** (logging + small helpers)

## Scope and non-goals

- **In scope**
  - Minimal, dependency-light helpers with sane defaults (timeouts, retries)
  - Code that is reusable across multiple repos and teams
  - Prefer explicit, boring interfaces over “framework magic”
- **Non-goals**
  - Product/business logic
  - Infrastructure-as-code (Terraform/CloudFormation) tooling (unless/ until it actually lives here)
  - A grab-bag of unrelated helpers

## Installation

Python: **3.14+**

> [!TIP]
> For local development, use editable installs so you can iterate quickly.

```bash
python3 -m pip install -U pip
python3 -m pip install -e .
```

Optional extras:

```bash
# Kubernetes client helpers
python3 -m pip install -e ".[kubernetes]"
```

## Quickstart

### Logging

```python
from platform_core.utils import logger

logger.info("hello from platform-core")
```

### Cloudflare API

```python
import os

from platform_core.cloudflare import cloudflare_api_session

with cloudflare_api_session(os.environ["CLOUDFLARE_API_TOKEN"]) as api:
    zones = api.make_api_request("GET", "/zones")
    print(zones)
```

### Vault (KV v2)

```python
import os

from platform_core.vault import VaultClient

client = VaultClient(vault_addr=os.environ["VAULT_ADDR"])
data = client.read_credentials(secrets_engine="secret", secret_path="team/app/credentials")
print(data)
```

### AWS

```python
from platform_core.aws import AWSUtils

aws = AWSUtils()
sts = aws.create_boto3_client("sts", "us-east-1")
print(sts.get_caller_identity()["Account"])
```

## Modules

- **`platform_core.utils`**
  - Logger setup + small helpers
- **`platform_core.aws`**
  - `AWSUtils` and helper managers (DynamoDB/SQS)
- **`platform_core.cloudflare`**
  - `CloudflareAPI` + `cloudflare_api_session`
  - Bounded retries for transient errors (429/5xx/408) with backoff
- **`platform_core.vault`**
  - `VaultClient` for KV v2 secret read/write with HTTP timeouts
- **`platform_core.kubernetes`**
  - `create_api_client()` (in-cluster config, else kubeconfig)

## Development

```bash
python3 -m pip install pre-commit
pre-commit install

# Update hook versions
pre-commit autoupdate

# Run checks
pre-commit run --all-files
```

CI runs the same checks on every PR/push via GitHub Actions.

## Contributing

See `.github/CONTRIBUTING.md`.

## Security

- Do not commit secrets (tokens, keys, credentials)
- `ggshield` runs on `pre-push` (set `GITGUARDIAN_API_KEY` to enable)
- Prefer least privilege for AWS and Cloudflare tokens

## License

See `LICENSE.md`.

- `src/platform_core/utils.py`: logging + core helpers
- `src/platform_core/aws.py`: AWS helpers
- `src/platform_core/cloudflare.py`: Cloudflare API helpers
- `src/platform_core/vault.py`: Vault helpers
