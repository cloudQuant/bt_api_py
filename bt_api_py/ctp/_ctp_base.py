"""Shared SWIG infrastructure for split CTP wrapper modules."""

from pathlib import Path
import weakref
from sys import float_info, stderr
from traceback import print_exception
from types import ModuleType


def _read_text_prefix(path: Path, limit: int = 256) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except Exception:
        return ""


def _build_ctp_import_diagnostic(import_error: Exception) -> str:
    base_message = str(import_error)
    module_dir = Path(__file__).resolve().parent
    framework_files = [
        module_dir / "thostmduserapi_se.framework" / "thostmduserapi_se",
        module_dir / "thosttraderapi_se.framework" / "thosttraderapi_se",
    ]
    lfs_pointer_files = []
    for framework_file in framework_files:
        prefix = _read_text_prefix(framework_file)
        if prefix.startswith("version https://git-lfs.github.com/spec/v1"):
            lfs_pointer_files.append(str(framework_file))
    if lfs_pointer_files:
        files = ", ".join(lfs_pointer_files)
        return (
            f"{base_message} | Git LFS pointer detected for CTP native SDK files: {files}. "
            "Run 'git lfs pull' in the bt_api_py repository, or restore the actual framework binaries."
        )
    return base_message


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
    def __init__(self, api_name: str, import_error: Exception) -> None:
        self._api_name = api_name
        self._import_error = import_error

    def _raise_unavailable(self):
        message = _build_ctp_import_diagnostic(self._import_error)
        raise RuntimeError(f"CTP native API '{self._api_name}' is unavailable: {message}")

    def __getattr__(self, name: str):
        if name == "GetApiVersion":
            return lambda *args, **kwargs: "fallback-ctp"
        return lambda *args, **kwargs: self._raise_unavailable()

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
                return lambda *args, **kwargs: _FallbackApiObject(ctor_name, self._import_error)
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
            return lambda *args, **kwargs: _FallbackApiObject(api_name, self._import_error)

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
    _ctp = _FallbackCtpModule(_ctp_import_error)


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
    "print_exception",
    "stderr",
    "weakref",
]
