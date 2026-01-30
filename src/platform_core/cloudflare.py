#!/usr/bin/env python3

"""
Cloudflare API Utility Library (cloudflare.py)

This module provides a reusable library for interacting with Cloudflare's API. It handles session
management, API request handling (including pagination), error handling, and retry logic with
exponential backoff.

Usage:
    - Import this module in your scripts to avoid boilerplate code
    - Use the `CloudflareAPI` class to make API calls
    - For new code, use the unified `make_api_request()` method which automatically handles
      both paginated and non-paginated responses

Example:
    with cloudflare_api_session(os.getenv("CLOUDFLARE_API_TOKEN")) as api:
        # Get zones (domains) using the recommended unified request method
        zones = api.make_api_request("GET", "/zones")

        # Update DNS record
        response = api.make_api_request(
            "PUT",
            f"/zones/{zone_id}/dns_records/{record_id}",
            json={"type": "A", "name": "acme.com", "content": "192.0.2.1"}
        )
"""

# Standard library
import os
import random
import time
import warnings
from contextlib import contextmanager
from typing import Any, Dict, Generator, List

# Third-party
from requests import RequestException, Response, Session, adapters

# Local
from .utils import logger, print_json

CLOUDFLARE_API_BASE_URL: str = "https://api.cloudflare.com/client/v4"
TIMEOUT: int = 30
DEFAULT_MAX_RETRIES: int = int(os.getenv("CLOUDFLARE_HTTP_MAX_RETRIES", "3"))
DEFAULT_RETRY_BASE_DELAY_SECONDS: float = float(os.getenv("CLOUDFLARE_HTTP_RETRY_BASE_DELAY_SECONDS", "1.0"))
DEFAULT_RETRY_MAX_DELAY_SECONDS: float = float(os.getenv("CLOUDFLARE_HTTP_RETRY_MAX_DELAY_SECONDS", "30.0"))


class CloudflareAPI:
    """
    Handles interaction with the Cloudflare API.

    This class manages API requests, including session management and error
    handling. It provides methods for making both paginated and non-paginated
    requests.

    Attributes:
        api_token (str): The Cloudflare API token for authentication.
        base_url (str): The base URL for the Cloudflare API.
        headers (dict[str, str]): The headers to be used in API requests.
        session (Session): The session object for making HTTP requests.
    """

    def __init__(self, api_token: str, base_url: str = CLOUDFLARE_API_BASE_URL):
        """
        Initializes the CloudflareAPI with an API token and optional base URL.

        Args:
            api_token (str): The Cloudflare API token.
            base_url (str, optional): The base URL for the Cloudflare API. Defaults to CLOUDFLARE_API_BASE_URL.
        """
        self.api_token = api_token
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {self.api_token}"}

        # Configure session with connection pooling
        self.session = Session()
        adapter = adapters.HTTPAdapter(
            pool_connections=10,  # Number of connection pools
            pool_maxsize=20,  # Connections per pool
            max_retries=3,  # Retries on connection errors
        )
        self.session.mount("https://", adapter)
        self.session.headers.update(self.headers)
        self._validate_api_token()

    def _validate_api_token(self) -> None:
        """
        Validates that the API token is provided.

        Raises:
            ValueError: If the API token is not provided.
        """
        if not self.api_token:
            raise ValueError("Cloudflare API token must be provided")

    def _build_url(self, endpoint: str) -> str:
        """
        Constructs the full API URL for a given endpoint.

        Args:
            endpoint (str): The API endpoint.

        Returns:
            str: The full API URL.
        """
        return f"{self.base_url}{endpoint}"

    def make_paginated_request(self, method: str, endpoint: str, **kwargs) -> List[Dict[str, Any]]:
        """
        Makes a paginated HTTP request to the Cloudflare API, retrieving all pages of data.

        Note: This method is maintained for backward compatibility. For new code,
        consider using make_api_request() which automatically handles both
        paginated and non-paginated responses.

        Args:
            method (str): HTTP method (e.g., GET).
            endpoint (str): The API endpoint.
            **kwargs: Additional keyword arguments for the request.

        Returns:
            List[Dict[str, Any]]: A list of results aggregated from all pages.

        Raises:
            RuntimeError: If the request fails after all retries.
        """
        warnings.warn(
            "make_request() is deprecated. Use make_api_request() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        all_results: List[Dict[str, Any]] = []
        url: str = self._build_url(endpoint)
        page: int = 1

        # Avoid passing "params" twice (caller-supplied + our inline params), which would
        # raise: TypeError: got multiple values for keyword argument 'params'
        base_params = kwargs.pop("params", {})
        if base_params is None:
            base_params = {}
        if not isinstance(base_params, dict):
            raise ValueError(f"params must be a dict when provided, got: {type(base_params)!r}")

        while True:
            # Use the simple_request method with error handling built-in
            api_response = self._simple_request(
                method,
                url,
                timeout=TIMEOUT,
                params={**base_params, "page": page},
                **kwargs,
            )

            # Debug logging with print_json
            logger.debug("API Response:")
            # Use print_json to display the response and get the original data back
            api_response = print_json(api_response)

            if "result" in api_response and isinstance(api_response["result"], list):
                all_results.extend(api_response["result"])
            else:
                raise ValueError(f"Unexpected response structure: {api_response}")

            result_info = api_response.get("result_info", {})
            total_pages: int = result_info.get("total_pages", 1)

            if page >= total_pages:
                break
            page += 1

        return all_results

    def _simple_request(self, method: str, url: str, **kwargs) -> Dict[str, Any]:
        """
        Makes a simple HTTP request with bounded retries for transient failures.

        Args:
            method (str): HTTP method (GET, POST, PUT, DELETE).
            url (str): The full API URL.
            **kwargs: Additional keyword arguments for the request.

        Returns:
            Dict[str, Any]: The API response.

        Raises:
            RequestException: If the request fails after retries or returns a non-2xx response.
        """
        timeout = kwargs.pop("timeout", TIMEOUT)
        max_retries = max(0, DEFAULT_MAX_RETRIES)

        def backoff_seconds(attempt: int) -> float:
            # Exponential backoff with small jitter.
            base = max(0.0, DEFAULT_RETRY_BASE_DELAY_SECONDS)
            cap = max(base, DEFAULT_RETRY_MAX_DELAY_SECONDS)
            raw = min(cap, base * (2**attempt))
            jitter = random.uniform(0.0, min(1.0, raw * 0.1))
            return raw + jitter

        for attempt in range(max_retries + 1):
            try:
                logger.debug("Making %s request to %s", method, url)
                response: Response = self.session.request(method, url, timeout=timeout, **kwargs)
            except RequestException:
                if attempt >= max_retries:
                    raise
                delay = backoff_seconds(attempt)
                logger.warning(
                    "Cloudflare request failed (attempt %s/%s); retrying in %.1fs", attempt + 1, max_retries + 1, delay
                )
                time.sleep(delay)
                continue

            # Retryable HTTP statuses: 429 rate limit, 5xx server errors, and 408 timeout.
            if response.status_code in (408, 429) or 500 <= response.status_code <= 599:
                if attempt < max_retries:
                    retry_after_header = response.headers.get("Retry-After", "")
                    retry_after: float | None = None
                    if response.status_code == 429 and retry_after_header:
                        try:
                            retry_after = float(int(retry_after_header))
                        except (TypeError, ValueError):
                            retry_after = None

                    delay = retry_after if retry_after is not None else backoff_seconds(attempt)
                    logger.warning(
                        "Cloudflare API returned %s (attempt %s/%s); retrying in %.1fs",
                        response.status_code,
                        attempt + 1,
                        max_retries + 1,
                        delay,
                    )
                    time.sleep(delay)
                    continue

            # Check if status code is not in 2xx range
            if not 200 <= response.status_code < 300:
                try:
                    error_body = response.text
                except UnicodeDecodeError:
                    error_body = "Unable to read response body"
                logger.error("Cloudflare API error status=%s response=%s", response.status_code, error_body)

            response.raise_for_status()  # This line must stay
            return response.json()

        # Defensive: the loop should always return or raise.
        raise RuntimeError("Unreachable: Cloudflare request retry loop exhausted")

    def make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """
        Makes a non-paginated HTTP request to the Cloudflare API.

        Note: This method is maintained for backward compatibility. For new code,
        consider using make_api_request() which automatically handles both
        paginated and non-paginated responses.

        Args:
            method (str): HTTP method (GET, POST, PUT, DELETE).
            endpoint (str): The API endpoint.
            **kwargs: Additional keyword arguments for the request.

        Returns:
            Dict[str, Any]: The API response.

        Raises:
            RuntimeError: If the request fails after all retries.
        """
        warnings.warn(
            "make_request() is deprecated. Use make_api_request() instead.",
            DeprecationWarning,
            stacklevel=2,
        )

        url: str = self._build_url(endpoint)
        response = self._simple_request(method, url, timeout=TIMEOUT, **kwargs)

        # Debug logging with print_json
        logger.debug("API Response:")
        # Use print_json to display the response and get the original data back
        return print_json(response)

    def make_api_request(self, method: str, endpoint: str, params=None, **kwargs) -> Any:
        """
        Makes a request to the Cloudflare API, automatically handling pagination if needed.

        This unified method detects whether the response requires pagination and handles
        it appropriately, combining the functionality of make_request and make_paginated_request.
        This is the recommended method for new code as it automatically handles both paginated
        and non-paginated responses. Pagination is only applied for GET requests.

        Args:
            method (str): HTTP method (GET, POST, PUT, DELETE).
            endpoint (str): The API endpoint.
            params (dict, optional): Query parameters to include in the request.
            **kwargs: Additional keyword arguments for the request.

        Returns:
            Any: Either a list of results (for paginated responses) or a single result object.
            None: If an error occurs or the response is invalid.

        Raises:
            ValueError: If response format is unexpected.
        """
        url: str = self._build_url(endpoint)

        if method.upper() != "GET":
            return self._handle_non_get_request(url, endpoint, method, params, **kwargs)

        return self._handle_paginated_get(url, endpoint, params, **kwargs)

    def _handle_non_get_request(self, url: str, endpoint: str, method: str, params, **kwargs) -> Any:
        """
        Handle non-GET requests (POST, PUT, DELETE).

        Args:
            url (str): The full API URL.
            endpoint (str): The API endpoint (for logging).
            method (str): HTTP method.
            params: Query parameters.
            **kwargs: Additional request arguments.

        Returns:
            Any: The result from the response, or None on error.
        """
        try:
            response = self._simple_request(method, url, params=params, **kwargs)
        except (RequestException, ValueError) as exc:
            logger.error("Error processing request to %s: %s", endpoint, exc)
            return None

        if isinstance(response, dict) and "result" in response:
            return response.get("result")
        return response

    def _handle_paginated_get(self, url: str, endpoint: str, params, **kwargs) -> Any:
        """
        Handle GET requests with automatic pagination.

        Args:
            url (str): The full API URL.
            endpoint (str): The API endpoint (for logging).
            params: Query parameters.
            **kwargs: Additional request arguments.

        Returns:
            Any: List of results (paginated) or single result, None on error.
        """
        all_results: List[Dict[str, Any]] = []
        page = 1
        per_page = 50  # Maximum allowed by Cloudflare API

        try:
            while True:
                request_params = params.copy() if params else {}
                request_params.update({"page": page, "per_page": per_page})

                response = self._simple_request("GET", url, params=request_params, **kwargs)

                if "result" not in response:
                    logger.warning("No 'result' key in response: %s", response)
                    break

                # Non-paginated response (no result_info)
                if "result_info" not in response:
                    return response["result"]

                all_results.extend(response["result"])

                # Check if we need to get more pages
                if page >= response.get("result_info", {}).get("total_pages", 0):
                    break
                page += 1

            logger.debug("Retrieved %s results from %s", len(all_results), endpoint)
            return all_results
        except (RequestException, ValueError, KeyError, TypeError) as exc:
            logger.error("Error processing request to %s: %s", endpoint, exc)
            return None

    def get_zone_id(self, zone_name: str) -> str:
        """
        Resolve a Cloudflare zone name to its zone identifier.

        Args:
            zone_name (str): The DNS zone name (e.g. acme.com).

        Returns:
            str: The Cloudflare zone ID.

        Raises:
            ValueError: If the zone cannot be found or the response is malformed.
        """
        logger.debug("Fetching zone ID for %s", zone_name)
        response = self.make_api_request("GET", "/zones", params={"name": zone_name})
        if not response:
            raise ValueError(f"Zone '{zone_name}' not found in Cloudflare account")
        try:
            return response[0]["id"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Unexpected response while resolving zone '{zone_name}': {response}") from exc

    def close(self) -> None:
        """Closes the session."""
        self.session.close()


@contextmanager
def cloudflare_api_session(api_token: str) -> Generator[CloudflareAPI, None, None]:
    """
    Context manager for creating and managing a CloudflareAPI session.

    Args:
        api_token (str): Cloudflare API token.

    Yields:
        CloudflareAPI: Instance of CloudflareAPI.
    """
    api: CloudflareAPI = CloudflareAPI(api_token)
    try:
        yield api
    finally:
        api.close()
        logger.info("Cloudflare API session closed.")
