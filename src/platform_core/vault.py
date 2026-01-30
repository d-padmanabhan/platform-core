#!/usr/bin/env python3

"""
Vault integration module for storing and retrieving secrets.

This module provides a simple interface to HashiCorp Vault for managing secrets using a
pre-configured Vault token (via `VAULT_TOKEN`).

Workflow:
    1. Initialize VaultClient with the Vault address.
    2. Use VAULT_TOKEN environment variable for authentication.
    3. Store or retrieve data using a KV v2 secrets engine and secret path.
    4. Handle responses and errors appropriately with logging.

Dependencies:
    - requests: For making HTTP requests to Vault API
    - utils.logger: For consistent logging across the application
"""

import os
from typing import Any, Dict

import requests
from requests.exceptions import RequestException

from .utils import logger

_DEFAULT_TIMEOUT_SECONDS = int(os.getenv("VAULT_HTTP_TIMEOUT_SECONDS", "30"))


class VaultClient:
    """
    Manages interactions with HashiCorp Vault using a Vault token.

    This class provides methods to store and retrieve data using the KV v2 secrets engine.

    Attributes:
        vault_addr (str): The URL of the Vault server
        token (str): The Vault token for authentication
    """

    def __init__(self, vault_addr: str | None = None) -> None:
        """
        Initialize Vault client using the VAULT_TOKEN environment variable.

        Args:
            vault_addr: The URL of the Vault server. Defaults to VAULT_ADDR env var.

        Raises:
            ValueError: If vault_addr or VAULT_TOKEN is missing or invalid.
        """
        self.vault_addr = (vault_addr or os.getenv("VAULT_ADDR") or "").rstrip("/")
        self.token = os.getenv("VAULT_TOKEN")
        self.timeout_seconds = _DEFAULT_TIMEOUT_SECONDS

        if not self.vault_addr:
            raise ValueError("Vault address must be provided via parameter or VAULT_ADDR environment variable.")
        if not self.token:
            raise ValueError("VAULT_TOKEN environment variable must be set.")

        self.session = requests.Session()
        self.session.headers.update({"X-Vault-Token": self.token, "Content-Type": "application/json"})

    def store_credentials(self, secrets_engine: str, secret_path: str, credentials: Dict[str, Any]) -> None:
        """
        Store a credentials dict in Vault (KV v2).

        Args:
            secrets_engine: KV v2 mount name (e.g., "secret" or "kv").
            secret_path: Secret path within the mount (e.g., "team/app/credentials").
            credentials: Dictionary containing credentials to store.

        Raises:
            RequestException: If the Vault API request fails.
            ValueError: If storing credentials fails due to invalid parameters or responses.
        """
        # KV v2 write endpoint: /v1/<mount>/data/<path>
        mount = secrets_engine.strip().strip("/")
        path = secret_path.strip().lstrip("/")
        vault_api_path = f"{mount}/data/{path}"
        payload = {"data": credentials}
        url = f"{self.vault_addr}/v1/{vault_api_path}"

        logger.info("Attempting to store secret data in Vault at %s", vault_api_path)
        try:
            response = self.session.post(url, json=payload, timeout=self.timeout_seconds)
            response.raise_for_status()
            logger.info("Successfully stored secret data in Vault at %s", vault_api_path)
        except RequestException:
            logger.error("Failed to store secret data in Vault at %s", vault_api_path, exc_info=True)
            raise

    def read_credentials(self, secrets_engine: str, secret_path: str) -> Dict[str, Any]:
        """
        Retrieve a credentials dict from Vault (KV v2).

        Args:
            secrets_engine: KV v2 mount name (e.g., "secret" or "kv").
            secret_path: Secret path within the mount (e.g., "team/app/credentials").

        Returns:
            Dict[str, Any]: Retrieved credentials.

        Raises:
            RequestException: If the Vault API request fails.
            FileNotFoundError: If credentials are not found at the specified path.
        """
        # KV v2 read endpoint: /v1/<mount>/data/<path>
        mount = secrets_engine.strip().strip("/")
        path = secret_path.strip().lstrip("/")
        vault_api_path = f"{mount}/data/{path}"
        url = f"{self.vault_addr}/v1/{vault_api_path}"

        logger.info("Attempting to retrieve secret data from Vault at %s", vault_api_path)
        try:
            response = self.session.get(url, timeout=self.timeout_seconds)
            if response.status_code == 404:
                logger.error("Credentials not found at path: %s", vault_api_path)
                raise FileNotFoundError(f"Credentials not found at path: {vault_api_path}")

            response.raise_for_status()
            result = response.json()
            credentials = result.get("data", {}).get("data", {})
            if not credentials:
                logger.error("No credentials found at path: %s", vault_api_path)
                raise ValueError(f"No credentials found at path: {vault_api_path}")

            logger.info("Successfully retrieved secret data from Vault at %s", vault_api_path)
            return credentials
        except RequestException:
            logger.error("Failed to retrieve secret data from Vault at %s", vault_api_path, exc_info=True)
            raise


def create_vault_client() -> VaultClient:
    """
    Factory function to create a VaultClient instance.

    Returns:
        VaultClient: Initialized VaultClient instance.

    Raises:
        ValueError: If initialization fails due to missing configuration.
    """
    try:
        return VaultClient()
    except ValueError as e:
        logger.error("Failed to create Vault client: %s", str(e))
        raise
