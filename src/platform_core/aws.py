#!/usr/bin/env python3

"""
AWS Utilities Module (aws.py)

This module provides utility classes and functions for interacting with AWS services,
including error handling, client/resource management, DynamoDB operations, and SQS operations.

Classes:
    AWSUtils: Manages AWS clients and provides utility methods for AWS operations.
    DynamoDBManager: Handles DynamoDB-specific operations.
    SQSManager: Manages SQS operations.

Functions:
    handle_aws_api_error: A decorator for handling AWS API errors.

Usage:
    from aws import AWSUtils, DynamoDBManager, SQSManager

    # Create an AWSUtils instance
    aws = AWSUtils()

    # Use AWSUtils methods
    account_id = aws.get_aws_account_id("us-west-2")

    # Create a DynamoDBManager instance
    dynamodb_manager = DynamoDBManager("my-table", aws.create_boto3_resource("dynamodb", "us-west-2"))

    # Use DynamoDBManager methods
    item = dynamodb_manager.get_item({"id": "12345"})
    dynamodb_manager.save_item({"id": "67890", "name": "Example Item"})

    # Create an SQSManager instance
    sqs_manager = SQSManager("us-west-2")

    # Use SQSManager methods
    messages = sqs_manager.receive_messages("https://sqs.us-west-2.amazonaws.com/123456789012/my-queue")
"""

from __future__ import annotations

import os
import time
from functools import wraps
from typing import Any, Callable

import boto3
from boto3.resources.base import ServiceResource
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    ParamValidationError,
    UnknownServiceError,
)

from .utils import logger


def handle_aws_api_error(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorator for handling AWS API errors.

    This decorator catches and logs AWS-specific exceptions, providing a consistent
    error handling mechanism for AWS API calls.

    Args:
        func: The function to decorate.

    Returns:
        The decorated function.

    Raises:
        Exception: A generic exception with a descriptive error message.
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except ParamValidationError as e:
            error_str = f"Parameter validation error: {str(e)}"
            logger.exception(error_str)
            raise ValueError(error_str) from e
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]
            status_code = e.response["ResponseMetadata"].get("HTTPStatusCode")
            request_id = e.response["ResponseMetadata"].get("RequestId")
            error_str = (
                f"AWS API error: {error_code} (Status: {status_code}, RequestId: {request_id}) - {error_message}"
            )
            logger.exception(error_str)
            raise RuntimeError(error_str) from e
        except NoCredentialsError as e:
            error_str = f"AWS credentials error: {str(e)}"
            logger.exception(error_str)
            raise RuntimeError(error_str) from e
        except BotoCoreError as e:
            error_str = f"AWS Botocore error: {str(e)}"
            logger.exception(error_str)
            raise RuntimeError(error_str) from e
        except Exception as e:
            error_str = f"Unexpected error: {str(e)}"
            logger.exception(error_str)
            raise RuntimeError(error_str) from e

    return wrapper


class AWSUtils:
    """
    A utility class for managing AWS clients and resources.

    This class provides methods for creating boto3 clients and resources (if available),
    as well as utility methods for common AWS operations.
    """

    ALLOWED_REGIONS: set[str] = {
        "us-east-1",
        "us-west-2",
        "us-east-2",
        "ca-central-1",
        "eu-west-1",
        "eu-west-2",
        "eu-central-1",
        "eu-north-1",
        "ap-southeast-2",
        "ap-southeast-1",
    }

    def __init__(self, **kwargs: Any) -> None:
        """
        Initialize the AWSUtils instance.

        Args:
            **kwargs: Additional keyword arguments for boto3 client/resource.
        """
        self.kwargs: dict[str, Any] = kwargs

    def _boto_kwargs(self, **overrides: Any) -> dict[str, Any]:
        """
        Merge instance-level boto3 kwargs with per-call overrides.

        Precedence:
        - Per-call kwargs win over instance kwargs

        Safety:
        - Do not allow passing values that would conflict with explicit parameters
          we pass to boto3 (e.g., region_name, config) to avoid TypeError due to
          duplicate keyword arguments.
        """
        merged: dict[str, Any] = {**self.kwargs, **overrides}
        merged.pop("region_name", None)
        merged.pop("config", None)
        return merged

    @classmethod
    def _validate_region(cls, region: str) -> str:
        """
        Validate the provided AWS region against a list of allowed regions.

        Args:
            region: The AWS region to validate.

        Returns:
            The validated AWS region.

        Raises:
            ValueError: If the provided region is not in the list of allowed regions.
        """
        if region not in cls.ALLOWED_REGIONS:
            raise ValueError(f"Invalid region: {region}. Please choose from {cls.ALLOWED_REGIONS}")
        return region

    @handle_aws_api_error
    def create_boto3_client(self, service_name: str, region_name: str, **kwargs: Any) -> BaseClient:
        """
        Create a boto3 client with custom retry configuration.

        Args:
            service_name: The AWS service for which to create the client.
            region_name: The AWS region.
            **kwargs: Additional keyword arguments to pass to boto3.client

        Returns:
            The created boto3 client.
        """
        region_name = self._validate_region(region_name)
        custom_boto3_retry_config: Config = Config(retries={"max_attempts": 5, "mode": "standard"})
        boto_kwargs = self._boto_kwargs(**kwargs)
        client: BaseClient = boto3.client(
            service_name,
            region_name=region_name,
            config=custom_boto3_retry_config,
            **boto_kwargs,
        )
        sts_client: BaseClient = boto3.client("sts", region_name=region_name, **boto_kwargs)
        account_id = sts_client.get_caller_identity().get("Account")

        logger.debug(
            "Created boto3 client for %s in region %s for account %s",
            service_name,
            region_name,
            account_id,
        )
        return client

    @handle_aws_api_error
    def create_boto3_resource(self, service_name: str, region_name: str, **kwargs: Any) -> ServiceResource:
        """
        Create a boto3 resource with custom retry configuration, if available.

        Args:
            service_name: The AWS service for which to create the resource.
            region_name: The AWS region.
            **kwargs: Additional keyword arguments to pass to boto3.resource

        Returns:
            The created boto3 resource.

        Raises:
            ValueError: If the service does not support a resource interface.
        """
        region_name = self._validate_region(region_name)
        if self._has_resource(service_name):
            custom_boto3_retry_config: Config = Config(retries={"max_attempts": 5, "mode": "standard"})
            boto_kwargs = self._boto_kwargs(**kwargs)
            resource: ServiceResource = boto3.resource(
                service_name,
                region_name=region_name,
                config=custom_boto3_retry_config,
                **boto_kwargs,
            )
            sts_client: BaseClient = boto3.client("sts", region_name=region_name, **boto_kwargs)
            account_id = sts_client.get_caller_identity().get("Account")
            logger.info(
                "Created boto3 resource for %s in region %s for account %s",
                service_name,
                region_name,
                account_id,
            )
            return resource
        error_message = f"Service '{service_name}' does not support resource interface."
        logger.error(error_message)
        raise ValueError(error_message)

    def _has_resource(self, service_name: str) -> bool:
        """
        Check if the service has a resource available in boto3.

        Args:
            service_name: The AWS service to check.

        Returns:
            True if the service has a resource, False otherwise.
        """
        try:
            boto3.resource(service_name)
            return True
        except (AttributeError, UnknownServiceError, BotoCoreError, ClientError):
            return False

    @handle_aws_api_error
    def sts_assume_role(self, role_arn: str, role_session_name: str, region: str, **kwargs: Any) -> dict[str, str]:
        """
        Assume an IAM role and return temporary security credentials.

        Args:
            role_arn: The ARN of the role to assume
            role_session_name: The name for the assumed role session
            region: The AWS region
            **kwargs: Additional arguments

        Returns:
            The temporary security credentials for the assumed role
        """
        boto_kwargs = self._boto_kwargs(**kwargs)
        client: BaseClient = boto3.client("sts", region_name=region, **boto_kwargs)
        response: dict[str, Any] = client.assume_role(RoleArn=role_arn, RoleSessionName=role_session_name)
        return response["Credentials"]

    @handle_aws_api_error
    def get_aws_account_id(self, region: str) -> str:
        """
        Retrieve the AWS account ID using the AWS STS client.

        Args:
            region: The AWS region.

        Returns:
            The AWS account ID.
        """
        client: BaseClient = boto3.client("sts", region_name=region, **self._boto_kwargs())
        account_id: str = client.get_caller_identity()["Account"]
        return account_id

    @staticmethod
    @handle_aws_api_error
    def verify_sts_temp_credentials() -> None:
        """
        Verify that temporary STS credentials are present in environment variables.

        This method checks for the presence of AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
        and AWS_SESSION_TOKEN environment variables, which indicates temporary STS
        credentials are being used.

        Raises:
            ValueError: If any of the required AWS environment variables are missing.
        """
        required_vars = [
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
        ]

        missing_vars = [var for var in required_vars if not os.getenv(var)]

        if missing_vars:
            error_msg = (
                f"Missing required STS environment variables: {', '.join(missing_vars)}. "
                f"Please ensure you have temporary STS credentials set."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        logger.info("AWS temporary STS credentials verified")

    @handle_aws_api_error
    def get_paginated_results(
        self,
        client: BaseClient,
        operation_name: str,
        result_key: str,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """
        Retrieve paginated results from an AWS API call.

        Args:
            client (boto3.client): The boto3 client to use for the API call.
            operation_name (str): The name of the API operation.
            result_key (str): The key in the response dict that contains the items.
            **kwargs: Additional keyword arguments to pass to the API call.

        Returns:
            A list of paginated results.
        """
        paginator = client.get_paginator(operation_name)
        try:
            response_iterator = paginator.paginate(**kwargs)
            results = []
            for page in response_iterator:
                results.extend(page.get(result_key, []))
            return results
        except ClientError as e:
            logger.error("Failed to paginate results for %s: %s", operation_name, e)
            raise


class DynamoDBManager:
    """
    A class for managing DynamoDB operations.
    """

    def __init__(
        self,
        table_name: str,
        dynamodb_resource: Any,
        dynamodb_client: BaseClient | None = None,
    ) -> None:
        """
        Initialize the DynamoDBManager instance.

        Args:
            table_name (str): The name of the DynamoDB table.
            dynamodb_resource (boto3.resource.Table): The DynamoDB table resource.
            dynamodb_client (boto3.client, optional): The DynamoDB client.
                                                     If not provided, derived from the resource.
        """
        self.table_name = table_name
        self.table = dynamodb_resource.Table(table_name)
        self.client = dynamodb_client or dynamodb_resource.meta.client

    @handle_aws_api_error
    def get_item(self, key: dict[str, Any], projection_expression: str | None = None) -> dict[str, Any] | None:
        """
        Retrieve a single item from the DynamoDB table.

        Args:
            key: The primary key of the item.
            projection_expression: Optional projection expression to specify attributes to retrieve.

        Returns:
            The retrieved item, or None if not found.
        """
        params: dict[str, Any] = {"Key": key}
        if projection_expression:
            params["ProjectionExpression"] = projection_expression

        response = self.table.get_item(**params)
        return response.get("Item")

    @handle_aws_api_error
    def save_item(self, item: dict[str, Any]) -> None:
        """
        Save an item to the DynamoDB table.

        Args:
            item: The item to save.
        """
        self.table.put_item(Item=item)
        logger.debug("Saved item to DynamoDB: %s", item)

    @handle_aws_api_error
    def batch_save_items(self, items: list[dict[str, Any]]) -> None:
        """
        Save multiple items to the DynamoDB table using BatchWriteItem.

        Args:
            items: The list of items to save.
        """
        if not items:
            logger.warning("No items to save in batch operation")
            return

        # Process in batches of 25 (DynamoDB limit)
        batch_size = 25
        for i in range(0, len(items), batch_size):
            batch = items[i : i + batch_size]
            request_items = {self.table_name: [{"PutRequest": {"Item": item}} for item in batch]}

            response = self.client.batch_write_item(RequestItems=request_items)

            # Retry unprocessed items with exponential backoff to prevent silent data loss
            unprocessed = response.get("UnprocessedItems", {})
            retries = 0
            max_retries = 5
            while unprocessed and retries < max_retries:
                retries += 1
                time.sleep(2**retries)
                logger.warning(
                    "Retrying %s unprocessed items (attempt %s/%s)",
                    sum(len(v) for v in unprocessed.values()),
                    retries,
                    max_retries,
                )
                response = self.client.batch_write_item(RequestItems=unprocessed)
                unprocessed = response.get("UnprocessedItems", {})

            if unprocessed:
                unprocessed_count = sum(len(v) for v in unprocessed.values())
                raise RuntimeError(
                    f"Failed to write {unprocessed_count} items to DynamoDB table "
                    f"{self.table_name} after {max_retries} retries"
                )

        logger.info("Batch saved %s items to DynamoDB table: %s", len(items), self.table_name)

    @handle_aws_api_error
    def delete_item(self, key: dict[str, Any]) -> None:
        """
        Delete an item from the DynamoDB table.

        Args:
            key: The primary key of the item to delete.
        """
        self.table.delete_item(Key=key)
        logger.info("Deleted item from DynamoDB with key: %s", key)

    @handle_aws_api_error
    def get_all_items(
        self,
        key_condition_expression: Any | None = None,
        expression_attribute_values: dict[str, Any] | None = None,
        index_name: str | None = None,
        projection_expression: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieve all items from the DynamoDB table, optionally using a specified index and projection.

        Args:
            key_condition_expression: The key condition expression for the query.
            expression_attribute_values: Values for expression attributes, if any.
            index_name: The name of the GSI to use for querying, if any.
            projection_expression: Optional projection expression to specify attributes to retrieve.

        Returns:
            A list of all items retrieved from the table.
        """
        items: list[dict[str, Any]] = []
        try:
            if key_condition_expression:
                response = self.table.query(
                    KeyConditionExpression=key_condition_expression,
                    ExpressionAttributeValues=expression_attribute_values,
                    IndexName=index_name,
                    ProjectionExpression=projection_expression,
                )
                items.extend(response.get("Items", []))
                while "LastEvaluatedKey" in response:
                    response = self.table.query(
                        KeyConditionExpression=key_condition_expression,
                        ExpressionAttributeValues=expression_attribute_values,
                        IndexName=index_name,
                        ProjectionExpression=projection_expression,
                        ExclusiveStartKey=response["LastEvaluatedKey"],
                    )
                    items.extend(response.get("Items", []))
            else:
                # Perform a Scan operation
                paginator = self.client.get_paginator("scan")
                operation_parameters: dict[str, Any] = {"TableName": self.table.name}
                if projection_expression:
                    operation_parameters["ProjectionExpression"] = projection_expression

                response_iterator = paginator.paginate(**operation_parameters)
                for page in response_iterator:
                    page_items = page.get("Items", [])
                    items.extend(page_items)
            logger.info("Retrieved %s items from DynamoDB.", len(items))
            return items
        except ClientError as e:
            logger.error("Failed to retrieve items from DynamoDB: %s", e)
            raise

    @handle_aws_api_error
    def query_items(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        index_name: str | None,
        key_condition_expression: str,
        expression_attribute_values: dict[str, Any],
        expression_attribute_names: dict[str, str] | None = None,
        projection_expression: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query items from DynamoDB with optional index and projection."""
        items: list[dict[str, Any]] = []
        query_params = {
            "KeyConditionExpression": key_condition_expression,
            "ExpressionAttributeValues": expression_attribute_values,
        }
        if index_name:
            query_params["IndexName"] = index_name
        if expression_attribute_names:
            query_params["ExpressionAttributeNames"] = expression_attribute_names
        if projection_expression:
            query_params["ProjectionExpression"] = projection_expression

        paginator = self.client.get_paginator("query")
        for page in paginator.paginate(TableName=self.table_name, **query_params):
            items.extend(page.get("Items", []))

        if index_name:
            logger.info("Queried %s items from DynamoDB using GSI '%s'.", len(items), index_name)
        else:
            logger.info("Queried %s items from DynamoDB.", len(items))
        return items


class SQSManager:
    """
    A class for managing SQS operations.

    This class provides methods for common SQS operations such as
    sending, receiving, and deleting messages.

    Attributes:
        sqs_client: The boto3 SQS client.
    """

    def __init__(self, region: str, **kwargs: Any):
        """
        Initialize the SQSManager instance.

        Args:
            region: The AWS region.
            **kwargs: Additional keyword arguments for the boto3 client.
        """
        self.sqs_client: BaseClient = AWSUtils(**kwargs).create_boto3_client("sqs", region)

    @handle_aws_api_error
    def send_message(
        self,
        queue_url: str,
        message_body: str,
        message_attributes: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """
        Send a message to an SQS queue.

        Args:
            queue_url: The URL of the SQS queue.
            message_body: The body of the message to send.
            message_attributes: Optional message attributes.

        Returns:
            The response from the SQS send_message call.
        """
        send_message_kwargs: dict[str, Any] = {
            "QueueUrl": queue_url,
            "MessageBody": message_body,
        }
        if message_attributes:
            send_message_kwargs["MessageAttributes"] = message_attributes

        response: dict[str, Any] = self.sqs_client.send_message(**send_message_kwargs)
        logger.info("Message sent to SQS queue: %s", queue_url)
        return response

    @handle_aws_api_error
    def receive_messages(
        self, queue_url: str, max_messages: int = 10, wait_time_seconds: int = 20
    ) -> list[dict[str, Any]]:
        """
        Receive messages from an SQS queue.

        Args:
            queue_url: The URL of the SQS queue.
            max_messages: The maximum number of messages to receive (1-10).
            wait_time_seconds: The duration (in seconds) for which the call waits for a message to arrive.

        Returns:
            A list of received messages.
        """
        response: dict[str, Any] = self.sqs_client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_time_seconds,
        )
        messages: list[dict[str, Any]] = response.get("Messages", [])
        logger.info("Received %s messages from SQS queue: %s", len(messages), queue_url)
        return messages

    @handle_aws_api_error
    def delete_message(self, queue_url: str, receipt_handle: str) -> None:
        """
        Delete a message from an SQS queue.

        Args:
            queue_url: The URL of the SQS queue.
            receipt_handle: The receipt handle of the message to delete.
        """
        self.sqs_client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
        logger.info("Deleted message from SQS queue: %s", queue_url)
