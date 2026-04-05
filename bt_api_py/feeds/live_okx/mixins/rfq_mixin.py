"""OKX API - RfqMixin
Auto-generated from request_base.py.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from bt_api_py.functions.utils import update_extra_data


class RfqMixin:
    """Mixin providing OKX API methods."""

    _params: Any
    asset_type: str
    exchange_name: str
    request: Callable[..., Any]
    submit: Callable[..., Any]
    async_request: Callable[..., Any]
    async_callback: Callable[..., Any]

    # ==================== RFQ (Request for Quote) / Block Trading ====================

    def _get_counterparties(
        self, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get RFQ counterparties list."""
        request_type = "get_counterparties"
        params: dict[str, Any] = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._get_counterparties_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_counterparties_normalize_function(
        input_data: Any, extra_data: Any
    ) -> tuple[Any, bool]:
        """Normalize RFQ counterparties response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def get_counterparties(self, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get RFQ counterparties list."""
        path, params, extra_data = self._get_counterparties(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_counterparties(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Async get RFQ counterparties list."""
        path, params, extra_data = self._get_counterparties(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _create_rfq(
        self,
        inst_id: Any,
        side: Any,
        sz: Any,
        ccy: Any = None,
        cl_ord_id: Any = None,
        tag: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Create RFQ."""
        request_type = "create_rfq"
        path = self._params.get_rest_path(request_type)
        params = {
            "instId": inst_id,
            "side": side,
            "sz": sz,
        }
        if ccy:
            params["ccy"] = ccy
        if cl_ord_id:
            params["clOrdId"] = cl_ord_id
        if tag:
            params["tag"] = tag
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._create_rfq_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _create_rfq_normalize_function(input_data: Any, extra_data: Any) -> tuple[Any, bool]:
        """Normalize create RFQ response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def create_rfq(
        self,
        inst_id: Any,
        side: Any,
        sz: Any,
        ccy: Any = None,
        cl_ord_id: Any = None,
        tag: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Create RFQ."""
        path, params, extra_data = self._create_rfq(
            inst_id, side, sz, ccy, cl_ord_id, tag, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_create_rfq(
        self,
        inst_id: Any,
        side: Any,
        sz: Any,
        ccy: Any = None,
        cl_ord_id: Any = None,
        tag: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async create RFQ."""
        path, params, extra_data = self._create_rfq(
            inst_id, side, sz, ccy, cl_ord_id, tag, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _cancel_rfq(
        self, rfq_id: Any, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Cancel RFQ."""
        request_type = "cancel_rfq"
        path = self._params.get_rest_path(request_type)
        params = {
            "rfqId": rfq_id,
            "instId": inst_id,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._cancel_rfq_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _cancel_rfq_normalize_function(input_data: Any, extra_data: Any) -> tuple[Any, bool]:
        """Normalize cancel RFQ response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def cancel_rfq(self, rfq_id: Any, inst_id: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """Cancel RFQ."""
        path, params, extra_data = self._cancel_rfq(rfq_id, inst_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_rfq(
        self, rfq_id: Any, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async cancel RFQ."""
        path, params, extra_data = self._cancel_rfq(rfq_id, inst_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _cancel_multiple_rfqs(
        self, rfq_ids: Any, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Cancel multiple RFQs."""
        request_type = "cancel_multiple_rfqs"
        path = self._params.get_rest_path(request_type)
        params = {
            "rfqIds": rfq_ids if isinstance(rfq_ids, str) else ",".join(rfq_ids),
            "instId": inst_id,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._cancel_multiple_rfqs_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _cancel_multiple_rfqs_normalize_function(
        input_data: Any, extra_data: Any
    ) -> tuple[Any, bool]:
        """Normalize cancel multiple RFQs response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def cancel_multiple_rfqs(
        self, rfq_ids: Any, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """Cancel multiple RFQs."""
        path, params, extra_data = self._cancel_multiple_rfqs(
            rfq_ids, inst_id, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_multiple_rfqs(
        self, rfq_ids: Any, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async cancel multiple RFQs."""
        path, params, extra_data = self._cancel_multiple_rfqs(
            rfq_ids, inst_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _cancel_all_rfqs(
        self, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Cancel all RFQs."""
        request_type = "cancel_all_rfqs"
        path = self._params.get_rest_path(request_type)
        params = {"instId": inst_id}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._cancel_all_rfqs_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _cancel_all_rfqs_normalize_function(input_data: Any, extra_data: Any) -> tuple[Any, bool]:
        """Normalize cancel all RFQs response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def cancel_all_rfqs(self, inst_id: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """Cancel all RFQs."""
        path, params, extra_data = self._cancel_all_rfqs(inst_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_all_rfqs(self, inst_id: Any, extra_data: Any = None, **kwargs: Any) -> None:
        """Async cancel all RFQs."""
        path, params, extra_data = self._cancel_all_rfqs(inst_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _execute_quote(
        self,
        quote_id: Any,
        inst_id: Any,
        side: Any,
        sz: Any,
        px: Any,
        ccy: Any = None,
        cl_ord_id: Any = None,
        tag: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Execute quote."""
        request_type = "execute_quote"
        path = self._params.get_rest_path(request_type)
        params = {
            "quoteId": quote_id,
            "instId": inst_id,
            "side": side,
            "sz": sz,
            "px": px,
        }
        if ccy:
            params["ccy"] = ccy
        if cl_ord_id:
            params["clOrdId"] = cl_ord_id
        if tag:
            params["tag"] = tag
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._execute_quote_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _execute_quote_normalize_function(input_data: Any, extra_data: Any) -> tuple[Any, bool]:
        """Normalize execute quote response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def execute_quote(
        self,
        quote_id: Any,
        inst_id: Any,
        side: Any,
        sz: Any,
        px: Any,
        ccy: Any = None,
        cl_ord_id: Any = None,
        tag: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Execute quote."""
        path, params, extra_data = self._execute_quote(
            quote_id, inst_id, side, sz, px, ccy, cl_ord_id, tag, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_execute_quote(
        self,
        quote_id: Any,
        inst_id: Any,
        side: Any,
        sz: Any,
        px: Any,
        ccy: Any = None,
        cl_ord_id: Any = None,
        tag: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async execute quote."""
        path, params, extra_data = self._execute_quote(
            quote_id, inst_id, side, sz, px, ccy, cl_ord_id, tag, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_quote_products(
        self, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get quote products list."""
        request_type = "get_quote_products"
        params: dict[str, Any] = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._get_quote_products_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_quote_products_normalize_function(
        input_data: Any, extra_data: Any
    ) -> tuple[Any, bool]:
        """Normalize quote products response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def get_quote_products(self, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get quote products list."""
        path, params, extra_data = self._get_quote_products(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_quote_products(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Async get quote products list."""
        path, params, extra_data = self._get_quote_products(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _set_quote_products(
        self, products: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Set quote products."""
        request_type = "set_quote_products"
        path = self._params.get_rest_path(request_type)
        params = {"products": products if isinstance(products, str) else json.dumps(products)}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._set_quote_products_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _set_quote_products_normalize_function(
        input_data: Any, extra_data: Any
    ) -> tuple[Any, bool]:
        """Normalize set quote products response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def set_quote_products(self, products: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """Set quote products."""
        path, params, extra_data = self._set_quote_products(products, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_set_quote_products(
        self, products: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async set quote products."""
        path, params, extra_data = self._set_quote_products(products, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _rfq_mmp_reset(
        self, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Reset MMP status for RFQ."""
        request_type = "rfq_mmp_reset"
        path = self._params.get_rest_path(request_type)
        params = {"instId": inst_id}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._rfq_mmp_reset_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _rfq_mmp_reset_normalize_function(input_data: Any, extra_data: Any) -> tuple[Any, bool]:
        """Normalize RFQ MMP reset response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def rfq_mmp_reset(self, inst_id: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """Reset MMP status for RFQ."""
        path, params, extra_data = self._rfq_mmp_reset(inst_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_rfq_mmp_reset(self, inst_id: Any, extra_data: Any = None, **kwargs: Any) -> None:
        """Async reset MMP status for RFQ."""
        path, params, extra_data = self._rfq_mmp_reset(inst_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _rfq_mmp_config(
        self,
        inst_id: Any,
        mode: Any,
        tier: Any,
        quote_limit: Any,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Set MMP for RFQ."""
        request_type = "rfq_mmp_config"
        path = self._params.get_rest_path(request_type)
        params = {
            "instId": inst_id,
            "mode": mode,
            "tier": str(tier),
            "quoteLimit": str(quote_limit),
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._rfq_mmp_config_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _rfq_mmp_config_normalize_function(input_data: Any, extra_data: Any) -> tuple[Any, bool]:
        """Normalize RFQ MMP config response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def rfq_mmp_config(
        self,
        inst_id: Any,
        mode: Any,
        tier: Any,
        quote_limit: Any,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Set MMP for RFQ."""
        path, params, extra_data = self._rfq_mmp_config(
            inst_id, mode, tier, quote_limit, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_rfq_mmp_config(
        self,
        inst_id: Any,
        mode: Any,
        tier: Any,
        quote_limit: Any,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async set MMP for RFQ."""
        path, params, extra_data = self._rfq_mmp_config(
            inst_id, mode, tier, quote_limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_rfq_mmp_config(
        self, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get MMP configuration for RFQ."""
        request_type = "get_rfq_mmp_config"
        path = self._params.get_rest_path(request_type)
        params = {"instId": inst_id}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._get_rfq_mmp_config_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_rfq_mmp_config_normalize_function(
        input_data: Any, extra_data: Any
    ) -> tuple[Any, bool]:
        """Normalize get RFQ MMP config response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def get_rfq_mmp_config(self, inst_id: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get MMP configuration for RFQ."""
        path, params, extra_data = self._get_rfq_mmp_config(inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_rfq_mmp_config(self, inst_id: Any, extra_data: Any = None, **kwargs: Any) -> None:
        """Async get MMP configuration for RFQ."""
        path, params, extra_data = self._get_rfq_mmp_config(inst_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _create_quote(
        self,
        inst_id: Any,
        side: Any,
        px: Any,
        sz: Any,
        cl_ord_id: Any,
        tif: Any,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Create RFQ quote."""
        request_type = "create_quote"
        path = self._params.get_rest_path(request_type)
        params = {
            "instId": inst_id,
            "side": side,
            "px": str(px),
            "sz": str(sz),
            "clOrdId": cl_ord_id,
            "tif": tif,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._create_quote_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _create_quote_normalize_function(input_data: Any, extra_data: Any) -> tuple[Any, bool]:
        """Normalize create quote response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def create_quote(
        self,
        inst_id: Any,
        side: Any,
        px: Any,
        sz: Any,
        cl_ord_id: Any,
        tif: Any,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Create RFQ quote."""
        path, params, extra_data = self._create_quote(
            inst_id, side, px, sz, cl_ord_id, tif, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_create_quote(
        self,
        inst_id: Any,
        side: Any,
        px: Any,
        sz: Any,
        cl_ord_id: Any,
        tif: Any,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async create RFQ quote."""
        path, params, extra_data = self._create_quote(
            inst_id, side, px, sz, cl_ord_id, tif, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _cancel_quote(
        self,
        inst_id: Any,
        quote_id: Any,
        cl_ord_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Cancel RFQ quote."""
        request_type = "cancel_quote"
        path = self._params.get_rest_path(request_type)
        params = {"instId": inst_id, "quoteId": quote_id}
        if cl_ord_id:
            params["clOrdId"] = cl_ord_id
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._cancel_quote_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _cancel_quote_normalize_function(input_data: Any, extra_data: Any) -> tuple[Any, bool]:
        """Normalize cancel quote response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def cancel_quote(
        self,
        inst_id: Any,
        quote_id: Any,
        cl_ord_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Cancel RFQ quote."""
        path, params, extra_data = self._cancel_quote(
            inst_id, quote_id, cl_ord_id, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_quote(
        self,
        inst_id: Any,
        quote_id: Any,
        cl_ord_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async cancel RFQ quote."""
        path, params, extra_data = self._cancel_quote(
            inst_id, quote_id, cl_ord_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _cancel_multiple_quotes(
        self,
        inst_id: Any,
        quote_ids: Any,
        cl_ord_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Cancel multiple RFQ quotes."""
        request_type = "cancel_multiple_quotes"
        path = self._params.get_rest_path(request_type)
        params = {"instId": inst_id}
        if isinstance(quote_ids, list):
            params["quoteIds"] = ",".join(quote_ids)
        else:
            params["quoteIds"] = quote_ids
        if cl_ord_id:
            params["clOrdId"] = cl_ord_id
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._cancel_multiple_quotes_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _cancel_multiple_quotes_normalize_function(
        input_data: Any, extra_data: Any
    ) -> tuple[Any, bool]:
        """Normalize cancel multiple quotes response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def cancel_multiple_quotes(
        self,
        inst_id: Any,
        quote_ids: Any,
        cl_ord_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Cancel multiple RFQ quotes."""
        path, params, extra_data = self._cancel_multiple_quotes(
            inst_id, quote_ids, cl_ord_id, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_multiple_quotes(
        self,
        inst_id: Any,
        quote_ids: Any,
        cl_ord_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async cancel multiple RFQ quotes."""
        path, params, extra_data = self._cancel_multiple_quotes(
            inst_id, quote_ids, cl_ord_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _cancel_all_quotes(
        self, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Cancel all RFQ quotes."""
        request_type = "cancel_all_quotes"
        path = self._params.get_rest_path(request_type)
        params = {"instId": inst_id}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._cancel_all_quotes_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _cancel_all_quotes_normalize_function(input_data: Any, extra_data: Any) -> tuple[Any, bool]:
        """Normalize cancel all quotes response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def cancel_all_quotes(self, inst_id: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """Cancel all RFQ quotes."""
        path, params, extra_data = self._cancel_all_quotes(inst_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_all_quotes(self, inst_id: Any, extra_data: Any = None, **kwargs: Any) -> None:
        """Async cancel all RFQ quotes."""
        path, params, extra_data = self._cancel_all_quotes(inst_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _rfq_cancel_all_after(
        self,
        inst_id: Any,
        cancel_after: Any,
        cl_ord_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Set timer to cancel all RFQ quotes."""
        request_type = "rfq_cancel_all_after"
        path = self._params.get_rest_path(request_type)
        params = {"instId": inst_id, "cancelAfter": str(cancel_after)}
        if cl_ord_id:
            params["clOrdId"] = cl_ord_id
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._rfq_cancel_all_after_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _rfq_cancel_all_after_normalize_function(
        input_data: Any, extra_data: Any
    ) -> tuple[Any, bool]:
        """Normalize RFQ cancel all after response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def rfq_cancel_all_after(
        self,
        inst_id: Any,
        cancel_after: Any,
        cl_ord_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Set timer to cancel all RFQ quotes."""
        path, params, extra_data = self._rfq_cancel_all_after(
            inst_id, cancel_after, cl_ord_id, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_rfq_cancel_all_after(
        self,
        inst_id: Any,
        cancel_after: Any,
        cl_ord_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async set timer to cancel all RFQ quotes."""
        path, params, extra_data = self._rfq_cancel_all_after(
            inst_id, cancel_after, cl_ord_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_rfqs(
        self,
        inst_type: Any = None,
        state: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get RFQs list."""
        request_type = "get_rfqs"
        params: dict[str, Any] = {}
        if inst_type:
            params["instType"] = inst_type
        if state:
            params["state"] = state
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        if limit:
            params["limit"] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._get_rfqs_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_rfqs_normalize_function(input_data: Any, extra_data: Any) -> tuple[Any, bool]:
        """Normalize get RFQs response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def get_rfqs(
        self,
        inst_type: Any = None,
        state: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get RFQs list."""
        path, params, extra_data = self._get_rfqs(
            inst_type, state, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_rfqs(
        self,
        inst_type: Any = None,
        state: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get RFQs list."""
        path, params, extra_data = self._get_rfqs(
            inst_type, state, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_rfq_quotes(
        self,
        rfq_id: Any = None,
        inst_id: Any = None,
        state: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get RFQ quotes list."""
        request_type = "get_rfq_quotes"
        params: dict[str, Any] = {}
        if rfq_id:
            params["rfqId"] = rfq_id
        if inst_id:
            params["instId"] = inst_id
        if state:
            params["state"] = state
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        if limit:
            params["limit"] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._get_rfq_quotes_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_rfq_quotes_normalize_function(input_data: Any, extra_data: Any) -> tuple[Any, bool]:
        """Normalize get RFQ quotes response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def get_rfq_quotes(
        self,
        rfq_id: Any = None,
        inst_id: Any = None,
        state: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get RFQ quotes list."""
        path, params, extra_data = self._get_rfq_quotes(
            rfq_id, inst_id, state, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_rfq_quotes(
        self,
        rfq_id: Any = None,
        inst_id: Any = None,
        state: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get RFQ quotes list."""
        path, params, extra_data = self._get_rfq_quotes(
            rfq_id, inst_id, state, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_rfq_trades(
        self,
        inst_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get RFQ trades."""
        request_type = "get_rfq_trades"
        params: dict[str, Any] = {}
        if inst_id:
            params["instId"] = inst_id
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        if limit:
            params["limit"] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._get_rfq_trades_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_rfq_trades_normalize_function(input_data: Any, extra_data: Any) -> tuple[Any, bool]:
        """Normalize get RFQ trades response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def get_rfq_trades(
        self,
        inst_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get RFQ trades."""
        path, params, extra_data = self._get_rfq_trades(
            inst_id, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_rfq_trades(
        self,
        inst_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get RFQ trades."""
        path, params, extra_data = self._get_rfq_trades(
            inst_id, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_public_rfq_trades(
        self,
        inst_type: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get public RFQ trades (multi-leg)."""
        request_type = "get_public_rfq_trades"
        params: dict[str, Any] = {}
        if inst_type:
            params["instType"] = inst_type
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        if limit:
            params["limit"] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._get_public_rfq_trades_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_public_rfq_trades_normalize_function(
        input_data: Any, extra_data: Any
    ) -> tuple[Any, bool]:
        """Normalize get public RFQ trades response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def get_public_rfq_trades(
        self,
        inst_type: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get public RFQ trades (multi-leg)."""
        path, params, extra_data = self._get_public_rfq_trades(
            inst_type, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_public_rfq_trades(
        self,
        inst_type: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get public RFQ trades (multi-leg)."""
        path, params, extra_data = self._get_public_rfq_trades(
            inst_type, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_block_tickers(
        self,
        inst_type: Any = None,
        uly: Any = None,
        inst_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get block tickers."""
        request_type = "get_block_tickers"
        params: dict[str, Any] = {}
        if inst_type:
            params["instType"] = inst_type
        if uly:
            params["uly"] = uly
        if inst_id:
            params["instId"] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._get_block_tickers_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_block_tickers_normalize_function(input_data: Any, extra_data: Any) -> tuple[Any, bool]:
        """Normalize get block tickers response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def get_block_tickers(
        self,
        inst_type: Any = None,
        uly: Any = None,
        inst_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get block tickers."""
        path, params, extra_data = self._get_block_tickers(
            inst_type, uly, inst_id, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_block_tickers(
        self,
        inst_type: Any = None,
        uly: Any = None,
        inst_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get block tickers."""
        path, params, extra_data = self._get_block_tickers(
            inst_type, uly, inst_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_block_ticker(
        self, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get single block ticker."""
        request_type = "get_block_ticker"
        params = {"instId": inst_id}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._get_block_ticker_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_block_ticker_normalize_function(input_data: Any, extra_data: Any) -> tuple[Any, bool]:
        """Normalize get block ticker response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def get_block_ticker(self, inst_id: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get single block ticker."""
        path, params, extra_data = self._get_block_ticker(inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_block_ticker(self, inst_id: Any, extra_data: Any = None, **kwargs: Any) -> None:
        """Async get single block ticker."""
        path, params, extra_data = self._get_block_ticker(inst_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_public_block_trades(
        self,
        inst_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get public block trades (single-leg)."""
        request_type = "get_public_block_trades"
        params: dict[str, Any] = {}
        if inst_id:
            params["instId"] = inst_id
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        if limit:
            params["limit"] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RfqMixin._get_public_block_trades_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_public_block_trades_normalize_function(
        input_data: Any, extra_data: Any
    ) -> tuple[Any, bool]:
        """Normalize get public block trades response."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        target_data = data if len(data) > 0 else []
        return target_data, status

    def get_public_block_trades(
        self,
        inst_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get public block trades (single-leg)."""
        path, params, extra_data = self._get_public_block_trades(
            inst_id, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_public_block_trades(
        self,
        inst_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get public block trades (single-leg)."""
        path, params, extra_data = self._get_public_block_trades(
            inst_id, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )
