"""Shared SWIG infrastructure for split CTP wrapper modules."""

from __future__ import annotations

import weakref
from sys import float_info, stderr
from traceback import print_exception
from types import ModuleType


# Import the low-level C/C++ module.
class _FallbackSwigHandle:
    def __init__(self) -> None:
        self._owned = True

    def own(self, value=None):
        if value is None:
            return self._owned
        self._owned = bool(value)
        return self._owned

    def disown(self) -> None:
        self._owned = False

    def __repr__(self) -> str:
        return f"<fallback-ctp-handle owned={self._owned}>"


class _FallbackApiObject:
    def __init__(self, api_name: str) -> None:
        self._api_name = api_name

    def __getattr__(self, name: str):
        if name == "GetApiVersion":
            return lambda *args, **kwargs: "fallback-ctp"
        return lambda *args, **kwargs: 0

    def __repr__(self) -> str:
        return f"<fallback-ctp-api {self._api_name}>"


class _FallbackCtpModule(ModuleType):
    def __init__(self, import_error: Exception) -> None:
        super().__init__("_ctp_fallback")
        self._import_error = import_error
        self._constants: dict[str, object] = {}

    def __getattr__(self, name: str):
        if name.startswith("THOST_"):
            return self._constants.setdefault(name, name)

        if name.startswith("new_"):
            ctor_name = name[4:]
            if ctor_name.endswith("Api"):
                return lambda *args, **kwargs: _FallbackApiObject(ctor_name)
            return lambda *args, **kwargs: _FallbackSwigHandle()

        if name.startswith("delete_"):
            return lambda *args, **kwargs: None

        if name.startswith("disown_"):

            def _disown(instance, *args, **kwargs):
                handle = getattr(instance, "this", None)
                if handle is not None and hasattr(handle, "disown"):
                    handle.disown()
                return None

            return _disown

        if name.endswith("_swiginit"):

            def _swiginit(instance, handle):
                object.__setattr__(instance, "this", handle)
                return None

            return _swiginit

        if name.endswith("_swigregister"):
            return lambda *args, **kwargs: None

        if name.endswith("_CreateFtdcMdApi") or name.endswith("_CreateFtdcTraderApi"):
            api_name = name.split("_", 1)[0]
            return lambda *args, **kwargs: _FallbackApiObject(api_name)

        if name.endswith("_GetApiVersion"):
            return lambda *args, **kwargs: "fallback-ctp"

        if name.endswith("_get"):
            prop_name = name.rsplit("_", 2)[1]

            def _getter(instance):
                values = instance.__dict__.get("_fallback_values", {})
                return values.get(prop_name)

            return _getter

        if name.endswith("_set"):
            prop_name = name.rsplit("_", 2)[1]

            def _setter(instance, value):
                values = instance.__dict__.setdefault("_fallback_values", {})
                values[prop_name] = value
                return None

            return _setter

        return lambda *args, **kwargs: None


try:
    if getattr(globals().get("__spec__"), "parent", None) or __package__ or "." in __name__:
        from . import _ctp
    else:
        import _ctp
except Exception as _ctp_import_error:
    import warnings as _warnings

    _warnings.warn(
        f"CTP C++ extension (_ctp) failed to load: {_ctp_import_error}. "
        "All CTP operations will silently no-op. "
        "If using Git LFS, run: git lfs install && git lfs pull",
        RuntimeWarning,
        stacklevel=1,
    )
    _ctp = _FallbackCtpModule(_ctp_import_error)


def is_ctp_native_loaded() -> bool:
    """Return True if the real SWIG C++ module loaded, False if using fallback."""
    return not isinstance(_ctp, _FallbackCtpModule)


def get_ctp_import_error():
    """Return the import error if using fallback module, else None."""
    if isinstance(_ctp, _FallbackCtpModule):
        return _ctp._import_error
    return None


def _swig_setattr_nondynamic_instance_variable(setter):
    def set_instance_attr(self, name, value):
        if name == "this":
            setter(self, name, value)
        elif name == "thisown":
            self.this.own(value)
        elif hasattr(self, name) and isinstance(getattr(type(self), name), property):
            setter(self, name, value)
        else:
            raise AttributeError(f"You cannot add instance attributes to {self}")

    return set_instance_attr


def _swig_setattr_nondynamic_class_variable(setter):
    def set_class_attr(cls, name, value):
        if hasattr(cls, name) and not isinstance(getattr(cls, name), property):
            setter(cls, name, value)
        else:
            raise AttributeError(f"You cannot add class attributes to {cls}")

    return set_class_attr


def _swig_add_metaclass(metaclass):
    """Slimmed-down version of six.add_metaclass for SWIG wrapper classes."""

    def wrapper(cls):
        return metaclass(cls.__name__, cls.__bases__, cls.__dict__.copy())

    return wrapper


class _SwigNonDynamicMeta(type):
    """Meta class to enforce nondynamic attributes on wrapped classes."""

    __setattr__ = _swig_setattr_nondynamic_class_variable(type.__setattr__)


def _swig_repr(self):
    values = []
    for key in vars(self.__class__):
        if key.startswith("_"):
            continue
        value = getattr(self, key)
        if isinstance(value, float):
            if value == float_info.max:
                values.append(f"{key}: None")
            else:
                values.append(f"{key}: {value:.2f}")
        elif isinstance(value, int):
            values.append(f"{key}: {value}")
        else:
            values.append(f'{key}: "{value}"')

    return f"<{self.__class__.__module__}.{self.__class__.__name__}; {', '.join(values)}>"


__all__ = [
    "_ctp",
    "_swig_repr",
    "_swig_setattr_nondynamic_instance_variable",
    "_swig_setattr_nondynamic_class_variable",
    "_swig_add_metaclass",
    "_SwigNonDynamicMeta",
    "is_ctp_native_loaded",
    "get_ctp_import_error",
    "print_exception",
    "stderr",
    "weakref",
]
