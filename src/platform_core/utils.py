#!/usr/bin/env python3

"""
Utility Library for Function Decoration and Logging (utils.py)

This library provides a set of utility functions and decorators for enhancing Python functions
with various capabilities such as execution time measurement, function call logging, memoization,
and exponential backoff for retry logic. It also includes a custom logger setup.

Usage:
    Import the desired decorators and functions into your Python script:

    from utility_lib import (
        setup_custom_logger,
        measure_execution_time,
        log_function_details,
        memoize,
        exponential_backoff
    )

    Then apply the decorators to your functions as needed:

    @measure_execution_time
    @log_function_details
    @memoize
    def your_function():
        # Your function code here

"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any, ParamSpec, TextIO, TypeVar, cast

P = ParamSpec("P")
T = TypeVar("T")
ExceptionTypes = tuple[type[BaseException], ...]


def _callable_name(func: Callable[..., Any]) -> str:
    """
    Best-effort name for a callable.

    `ty` is stricter than "runtime Python" here: not every callable has `__name__`.
    """
    return getattr(func, "__name__", func.__class__.__name__)


def setup_custom_logger(name: str) -> logging.Logger:
    """
    Create and configure a logger with a custom format and environment-specific behavior.
    This function sets up a logger with a specific name and configures it with:
    - Log level from LOG_LEVEL environment variable (defaults to INFO)
    - UTC timestamp formatting for non-Lambda environments
    - Stream handler that outputs to console (skipped in AWS Lambda to avoid duplicate logs)
        name (str): The name of the logger to create or retrieve.
        logging.Logger: The configured logger instance with appropriate handlers and formatting.
    Environment Variables:
        LOG_LEVEL: Sets the logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
                  Defaults to INFO if not specified.
        AWS_LAMBDA_FUNCTION_NAME: When present, indicates Lambda environment and
                                 skips handler configuration to prevent duplicate logs.
    Note:
        In AWS Lambda environments, the function returns a logger without adding
        custom handlers since Lambda automatically captures and formats log output.
    Args:
        name (str): The name of the logger.
    Returns:
        logging.Logger: The configured logger instance.
    """
    logger_obj: logging.Logger = logging.getLogger(name)

    # Get log level from environment variable, default to INFO
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger_obj.setLevel(getattr(logging, log_level, logging.INFO))

    # Only add handler if not running in Lambda (to avoid duplicate logs)
    if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):

        class UTCFormatter(logging.Formatter):
            """Formatter that forces UTC timestamps."""

            @staticmethod
            def converter(seconds: float | None) -> time.struct_time:
                """Convert timestamp to UTC struct_time."""
                return time.gmtime(seconds)

        formatter: UTCFormatter = UTCFormatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%c %Z")

        if not logger_obj.handlers:
            stream_handler: logging.StreamHandler[TextIO] = logging.StreamHandler()
            stream_handler.setFormatter(formatter)
            logger_obj.addHandler(stream_handler)

    return logger_obj


## Initialize the logger
logger: logging.Logger = setup_custom_logger(__name__)


def measure_execution_time(func: Callable[P, T]) -> Callable[P, T]:
    """
    Decorator to measure the execution time of a function.

    This decorator wraps the function and logs the time taken for its execution.

    Args:
        func: The function to be decorated.

    Returns:
        A wrapped function that measures and logs its execution time.
    """

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        start_time = time.time()
        result = func(*args, **kwargs)
        execution_time = time.time() - start_time
        logger.info("%s took %.4f seconds to execute.", _callable_name(func), execution_time)
        return result

    return wrapper


def log_function_details(func: Callable[P, T]) -> Callable[P, T]:
    """
    Decorator to log function call details - arguments and return value.

    This decorator logs the function name, its arguments, and its return value.

    Args:
        func: The function to be decorated.

    Returns:
        A wrapped function that logs its call details and return value.
    """

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        args_repr = [repr(a) for a in args]
        kwargs_repr = [f"{k}={v!r}" for k, v in kwargs.items()]
        signature = ", ".join(args_repr + kwargs_repr)
        logger.debug("Calling %s with (%s)", _callable_name(func), signature)
        result = func(*args, **kwargs)
        logger.debug("%s returned %r", _callable_name(func), result)
        return result

    return wrapper


def memoize(func: Callable[P, T]) -> Callable[P, T]:
    """
    Decorator to cache the results of a function call based on its arguments.

    This decorator implements memoization using an LRU cache with a maximum size
    of 256 entries to prevent unbounded memory growth. It supports both positional
    and keyword arguments, and tracks cache hits/misses.

    Args:
        func: The function to be decorated.

    Returns:
        A wrapped function with caching capability.
    """
    MAX_CACHE_SIZE = 256  # pylint: disable=invalid-name

    @lru_cache(maxsize=MAX_CACHE_SIZE)
    def cached_call(args_key: tuple[Any, ...], kwargs_items: tuple[tuple[str, Any], ...]) -> T:
        if kwargs_items:
            return func(*args_key, **dict(kwargs_items))
        return func(*args_key)  # type: ignore[call-arg]

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        # LRU cache requires hashable keys. This decorator can only cache calls
        # whose args/kwargs values are hashable (same constraint as the old dict-key approach).
        args_key: tuple[Any, ...] = tuple(args)
        kwargs_items: tuple[tuple[str, Any], ...] = tuple(sorted(kwargs.items()))

        # Pre-flight hashability check to avoid double-invoking the wrapped function
        # (if the function itself raises TypeError, we should not swallow/retry it).
        hash((args_key, kwargs_items))

        return cached_call(args_key, kwargs_items)

    def cache_info() -> dict[str, int | None]:
        """
        Provides information about the cache.

        Returns:
            A dictionary containing cache statistics.
        """
        info = cached_call.cache_info()  # pylint: disable=no-value-for-parameter
        return {"hits": info.hits, "misses": info.misses, "size": info.currsize, "maxsize": info.maxsize}

    def cache_clear() -> None:
        """Clear the cache."""
        cached_call.cache_clear()

    # Avoid direct attribute assignment to satisfy stricter type checkers (e.g., mypy/ty).
    setattr(wrapper, "cache_info", cache_info)
    setattr(wrapper, "cache_clear", cache_clear)
    return cast(Callable[P, T], wrapper)


def exponential_backoff(
    max_retries: int,
    exceptions: ExceptionTypes = (Exception,),
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator to retry a function with exponential backoff in case of exceptions.

    This decorator implements an exponential backoff strategy for retrying functions that may fail.

    Args:
        max_retries: Maximum number of retries.

    Returns:
        A decorator function.

    Raises:
        RuntimeError: If the maximum number of retries is exceeded.
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            retries = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    if retries >= max_retries:
                        logger.error(
                            "Max retries (%s) reached. Last error: %s",
                            max_retries,
                            exc,
                        )
                        raise RuntimeError(f"Failed after {max_retries} retries.") from exc
                    retries += 1
                    wait_time = 2**retries
                    logger.info(
                        "Attempt %s/%s. Retrying in %s seconds...",
                        retries,
                        max_retries,
                        wait_time,
                    )
                    time.sleep(wait_time)

        return wrapper

    return decorator


def format_json(data: Any) -> str:
    """
    Format JSON data with proper indentation.

    Args:
        data (Any): The data to be formatted in JSON format.

    Returns:
        str: The formatted JSON string.
    """
    return json.dumps(data, indent=4)


def print_json(data: Any) -> Any:
    """
    Pretty print JSON data and return the original data.

    Args:
        data (Any): The data to be printed in JSON format.

    Returns:
        Any: The original data (unchanged).
    """
    print(format_json(data))
    return data


def load_json_file(file_name: str) -> dict[str, Any]:
    """
    Loads and parses a JSON file from the specified folder path.

    Args:
        file_name (str): The name of the JSON file to read (without the folder path)

    Returns:
        dict: The parsed JSON data as a dictionary

    Raises:
        FileNotFoundError: If the specified file doesn't exist
        json.JSONDecodeError: If the file contains invalid JSON

    Example:
        api_config = load_json_file("api-config.json")
    """
    file_path = Path(file_name)

    logger.debug("Loading JSON file from: %s", file_path)

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        logger.debug("Successfully loaded JSON file: %s", file_name)
        if not isinstance(data, dict):
            raise ValueError(f"JSON root must be an object: {file_name}")
        return data
    except FileNotFoundError:
        logger.error("JSON file not found: %s", file_path)
        raise
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON in file %s: %s", file_path, exc)
        raise


def save_json_file(file_name: str, data: Any, indent: int = 4) -> None:
    """
    Writes data to a JSON file with specified indentation.

    Args:
        file_name (str): The name of the JSON file to write (without the folder path)
        data (Any): The data to be written to the JSON file
        indent (int, optional): The indentation level for the JSON file. Defaults to 4.

    Raises:
        IOError: If there is an error writing to the file
        TypeError: If the data cannot be serialized to JSON

    Example:
        save_json_file("api-config.json", api_config)
    """
    file_path = Path(file_name)

    logger.debug("Saving data to JSON file: %s", file_path)

    try:
        with file_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
        logger.debug("Successfully saved data to JSON file: %s", file_name)
    except TypeError as exc:
        logger.error("Failed to serialize data to JSON for file %s: %s", file_path, exc)
        raise
    except IOError as exc:
        logger.error("Failed to write to JSON file %s: %s", file_path, exc)
        raise


def to_kebab_case(input_string: str) -> str:
    """
    Convert a string to kebab case.

    This function transforms an input string into kebab case by following these steps:
    1. Replace any non-word character (except spaces) with a space
    2. Convert the string to lowercase
    3. Replace consecutive spaces with a single space
    4. Replace spaces with hyphens

    Args:
        input_string (str): The string to be converted to kebab case.

    Returns:
        str: The input string converted to kebab case.

    Example:
        >>> to_kebab_case("Hello World!")
        "hello-world"
        >>> to_kebab_case("AppStream_2.0")
        "appstream-2-0"
    """
    # Replace any non-word character (except spaces) with a space
    word_separated = re.sub(r"[^\w\s]", " ", input_string)

    # Convert to lowercase
    lowercased = word_separated.lower()

    # Replace consecutive spaces with a single space
    single_spaced = re.sub(r"\s+", " ", lowercased)

    # Replace spaces with hyphens
    kebab_case = single_spaced.replace(" ", "-")

    return kebab_case


def validate_environment_variables(required_vars: list[str]) -> tuple[str, ...]:
    """
    Validate required environment variables and return their values.

    Args:
        required_vars (list[str]): A list of environment variable names that must be set.

    Returns:
        tuple[str, ...]: A tuple containing the values of the required environment variables in the order they
                         were specified in the input list.

    Raises:
        SystemExit: If any required environment variable is missing.

    Example:
        >>> os.environ["AWS_DEFAULT_REGION"] = "us-west-2"
        >>> os.environ["AWS_PROFILE"] = "dev-profile"
        >>> required = ["AWS_DEFAULT_REGION", "AWS_PROFILE"]
        >>> region, profile = validate_environment_variables(required)
        >>> print(region, profile)
        'us-west-2' 'dev-profile'
    """
    env_values = {key: os.getenv(key) for key in required_vars}
    missing_vars = [key for key, value in env_values.items() if not value]

    if missing_vars:
        logger.error("The following required environment variables are not set: %s", ", ".join(missing_vars))
        sys.exit(1)

    # After validation, we know all values are non-None
    return tuple(str(env_values[key]) for key in required_vars)
