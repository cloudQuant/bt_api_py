"""
OKX API - StatusMixin
Auto-generated from request_base.py
"""

from typing import Any

from bt_api_py.functions.utils import update_extra_data


class StatusMixin:
    """Mixin providing OKX API methods."""

    # ==================== Status/Announcement APIs ====================

    def _get_system_status(self, state: Any = None, extra_data: Any = None, **kwargs: Any) -> None:
        """Get system status (maintenance, degraded, etc.)
        Args:
            state: Status type. "scheduled" for maintenance announcements. Default is empty for current system status.
            extra_data: extra_data, default is None, can be a dict passed by user
            kwargs: pass key-worded, variable-length arguments.
        """
        request_type = "get_system_status"
        params = {}
        if state is not None:
            params["state"] = state
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "SYSTEM",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": StatusMixin._get_system_status_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_system_status_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize system status response
        Returns: (status_list, status_bool) where status_list contains system status data
        """
        status = input_data.get("code") == "0"
        if "data" not in input_data or not input_data["data"]:
            return [], status
        return input_data["data"], status

    def get_system_status(self, state: Any = None, extra_data: Any = None, **kwargs: Any) -> None:
        """Get system status
        Args:
            state: Status type. "scheduled" for maintenance announcements. Default is empty for current system status.
            extra_data: extra_data, default is None, can be a dict passed by user
        """
        path, params, extra_data = self._get_system_status(state, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_system_status(
        self, state: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get system status
        Args:
            state: Status type. "scheduled" for maintenance announcements. Default is empty for current system status.
            extra_data: extra_data, default is None, can be a dict passed by user
        """
        path, params, extra_data = self._get_system_status(state, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_announcements(
        self,
        announcement_type: Any = None,
        page: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get announcements
        Args:
            announcement_type: Announcement type. Default is empty for all types.
            page: Page number. Default is 1.
            limit: Number of results per page. Default is 10. Maximum is 100.
            extra_data: extra_data, default is None, can be a dict passed by user
            kwargs: pass key-worded, variable-length arguments.
        """
        request_type = "get_announcements"
        params = {}
        if announcement_type is not None:
            params["announcementType"] = announcement_type
        if page is not None:
            params["page"] = str(page)
        if limit is not None:
            params["limit"] = str(limit)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "SYSTEM",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": StatusMixin._get_announcements_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_announcements_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize announcements response
        Returns: (announcement_list, status_bool) where announcement_list contains announcement data
        """
        status = input_data.get("code") == "0"
        if "data" not in input_data or not input_data["data"]:
            return [], status
        # Return the announcement details directly
        if "details" in input_data["data"][0]:
            return input_data["data"][0]["details"], status
        return input_data["data"], status

    def get_announcements(
        self,
        announcement_type: Any = None,
        page: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get announcements
        Args:
            announcement_type: Announcement type. Default is empty for all types.
            page: Page number. Default is 1.
            limit: Number of results per page. Default is 10. Maximum is 100.
            extra_data: extra_data, default is None, can be a dict passed by user
        """
        path, params, extra_data = self._get_announcements(
            announcement_type, page, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_announcements(
        self,
        announcement_type: Any = None,
        page: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get announcements
        Args:
            announcement_type: Announcement type. Default is empty for all types.
            page: Page number. Default is 1.
            limit: Number of results per page. Default is 10. Maximum is 100.
            extra_data: extra_data, default is None, can be a dict passed by user
        """
        path, params, extra_data = self._get_announcements(
            announcement_type, page, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_announcement_types(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Get announcement types
        Args:
            extra_data: extra_data, default is None, can be a dict passed by user
            kwargs: pass key-worded, variable-length arguments.
        """
        request_type = "get_announcement_types"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "SYSTEM",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": StatusMixin._get_announcement_types_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_announcement_types_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize announcement types response
        Returns: (type_list, status_bool) where type_list contains announcement type data
        """
        status = input_data.get("code") == "0"
        if "data" not in input_data or not input_data["data"]:
            return [], status
        # Return the announcement types directly
        if "announcementType" in input_data["data"][0]:
            return input_data["data"][0]["announcementType"], status
        return input_data["data"], status

    def get_announcement_types(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Get announcement types
        Args:
            extra_data: extra_data, default is None, can be a dict passed by user
        """
        path, params, extra_data = self._get_announcement_types(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_announcement_types(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Async get announcement types
        Args:
            extra_data: extra_data, default is None, can be a dict passed by user
        """
        path, params, extra_data = self._get_announcement_types(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )
