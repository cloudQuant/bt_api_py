"""
Balancer REST API request base class.

Handles GraphQL queries to Balancer's API. Balancer uses GraphQL instead of
traditional REST endpoints, so this class provides GraphQL query building and
response parsing.
"""

import json
from typing import Any

from bt_api_py.containers.exchanges.balancer_exchange_data import (
    BalancerExchangeData,
    GqlChain,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class BalancerRequestData(Feed):
    """Balancer REST API Feed base class.

    Handles GraphQL queries to Balancer's API. Since Balancer is a DEX with
    GraphQL-only API, traditional signing is not required for public queries.
    """

    @classmethod
    def _capabilities(cls):
        """Declare supported capabilities for Balancer."""
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "BALANCER___DEX")
        chain_value = kwargs.get("chain", GqlChain.MAINNET)
        # Convert string to enum if needed
        if isinstance(chain_value, str):
            try:
                self.chain = GqlChain(chain_value)
            except ValueError:
                self.chain = GqlChain.MAINNET
        else:
            self.chain = chain_value
        self.logger_name = kwargs.get("logger_name", "balancer_feed.log")
        self._params = BalancerExchangeData(self.chain)
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

        # Use HttpClient for GraphQL requests
        self._http_client = HttpClient(venue=self.exchange_name, timeout=30)

    def _build_graphql_query(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Build a GraphQL query payload.

        Args:
            query: GraphQL query string
            variables: Optional variables for the query

        Returns:
            Dictionary with query and variables
        """
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        return payload

    def _execute_graphql_query(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        extra_data=None,
    ) -> RequestData:
        """Execute a GraphQL query.

        Args:
            query: GraphQL query string
            variables: Optional variables for the query
            extra_data: Extra data to attach to response

        Returns:
            RequestData with parsed response
        """
        path = self._params.get_rest_path("graphql")
        _, url = path.split(" ", 1)

        payload = self._build_graphql_query(query, variables)

        headers = {
            "Content-Type": "application/json",
        }

        try:
            response = self._http_client.request(
                method="POST",
                url=url,
                headers=headers,
                json_data=payload,
            )

            # Check for GraphQL errors
            if "errors" in response:
                self.request_logger.error(
                    f"GraphQL errors: {response['errors']}"
                )

            return RequestData(response, extra_data)

        except Exception as e:
            self.request_logger.error(f"GraphQL query failed: {e}")
            raise

    async def _async_execute_graphql_query(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        extra_data=None,
    ) -> RequestData:
        """Execute a GraphQL query asynchronously.

        Args:
            query: GraphQL query string
            variables: Optional variables for the query
            extra_data: Extra data to attach to response

        Returns:
            RequestData with parsed response
        """
        path = self._params.get_rest_path("graphql")
        _, url = path.split(" ", 1)

        payload = self._build_graphql_query(query, variables)

        headers = {
            "Content-Type": "application/json",
        }

        try:
            response = await self._http_client.async_request(
                method="POST",
                url=url,
                headers=headers,
                json_data=payload,
            )

            if "errors" in response:
                self.async_logger.error(
                    f"GraphQL errors: {response['errors']}"
                )

            return RequestData(response, extra_data)

        except Exception as e:
            self.async_logger.error(f"Async GraphQL query failed: {e}")
            raise

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request function (legacy interface).

        For Balancer, use _execute_graphql_query instead.
        """
        raise NotImplementedError(
            "Use _execute_graphql_query() for Balancer GraphQL queries"
        )

    async def async_request(
        self, path, params=None, body=None, extra_data=None, timeout=5
    ):
        """Async HTTP request function (legacy interface).

        For Balancer, use _async_execute_graphql_query instead.
        """
        raise NotImplementedError(
            "Use _async_execute_graphql_query() for Balancer GraphQL queries"
        )

    def push_data_to_queue(self, data):
        """Push data to the queue."""
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            raise RuntimeError("Queue not initialized")

    def connect(self) -> None:
        """No-op for HTTP-based GraphQL API."""
        pass

    def disconnect(self) -> None:
        """No-op for HTTP-based GraphQL API."""
        pass

    def is_connected(self) -> bool:
        """Always return True for HTTP-based GraphQL API."""
        return True
