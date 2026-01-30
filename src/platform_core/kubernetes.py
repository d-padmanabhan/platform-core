"""
Kubernetes helpers.

This module provides a thin, dependency-light wrapper around the official Kubernetes Python
client to standardize:

- Loading config (in-cluster vs local kubeconfig)
- Creating an ApiClient with consistent initialization

The Kubernetes dependency is optional.
Install with:

    python3 -m pip install -e ".[kubernetes]"
"""

from __future__ import annotations

import importlib
from typing import Any


def _require_kubernetes() -> Any:
    try:
        return importlib.import_module("kubernetes")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Kubernetes helpers require the optional dependency. "
            'Install with: python3 -m pip install -e ".[kubernetes]"'
        ) from exc


def create_api_client(*, context: str | None = None) -> Any:
    """
    Create a Kubernetes ApiClient.

    Behavior:
    - If running in-cluster, uses in-cluster config
    - Otherwise, loads local kubeconfig (optionally selecting `context`)
    """
    kube = _require_kubernetes()

    config = kube.config
    client = kube.client

    try:
        config.load_incluster_config()
    except Exception:  # pylint: disable=broad-exception-caught
        config.load_kube_config(context=context)

    return client.ApiClient()
