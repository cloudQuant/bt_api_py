"""
Balancer REST API request base class.

Handles GraphQL queries to Balancer's API. Balancer uses GraphQL instead of
traditional REST endpoints, so this class provides GraphQL query building and
response parsing.
"""

from typing import Any

from bt_api_py.containers.exchanges.balancer_exchange_data import (
    BalancerExchangeData,
    GqlChain,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.logging_factory import get_logger


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
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
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
        self.request_logger = get_logger("balancer_feed")
        self.async_logger = get_logger("balancer_feed")

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
                self.request_logger.error(f"GraphQL errors: {response['errors']}")

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
                self.async_logger.error(f"GraphQL errors: {response['errors']}")

            return RequestData(response, extra_data)

        except Exception as e:
            self.async_logger.error(f"Async GraphQL query failed: {e}")
            raise

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """Execute a request. For Balancer, delegates to GraphQL execution.

        If extra_data contains '_graphql_query' and '_graphql_variables',
        executes a GraphQL query. Otherwise returns a RequestData wrapper.
        """
        if extra_data and "_graphql_query" in extra_data:
            query = extra_data.pop("_graphql_query")
            variables = extra_data.pop("_graphql_variables", None)
            return self._execute_graphql_query(query, variables, extra_data)
        # Fallback: wrap body/params as RequestData directly
        return RequestData(body or params or {}, extra_data)

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Async request. For Balancer, delegates to async GraphQL execution.

        If extra_data contains '_graphql_query' and '_graphql_variables',
        executes an async GraphQL query.
        """
        if extra_data and "_graphql_query" in extra_data:
            query = extra_data.pop("_graphql_query")
            variables = extra_data.pop("_graphql_variables", None)
            return await self._async_execute_graphql_query(query, variables, extra_data)
        return RequestData(body or params or {}, extra_data)

    def async_callback(self, future):
        """Callback function for async requests, push result to data_queue."""
        try:
            result = future.result()
            if result is not None:
                self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.error(f"Async callback error: {e}")

    # ── Standard Interface: get_server_time ───────────────────────

    def _get_server_time(self, extra_data=None, **kwargs):
        """Prepare server time request. Returns (path, params, extra_data).

        Note: Balancer is a DEX without a server time endpoint.
        Returns current local time as a fallback.
        """
        import time

        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": "",
                "asset_type": getattr(self, "asset_type", "DEX"),
                "request_type": "get_server_time",
                "server_time": time.time(),
            }
        )
        return "GET /server_time", {}, extra_data

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time. Returns RequestData.

        Balancer is a DEX — returns local timestamp as proxy.
        """
        import time

        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        return RequestData({"server_time": time.time()}, extra_data)

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
