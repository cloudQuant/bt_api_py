"""Shared SWIG infrastructure for split CTP wrapper modules."""

from sys import float_info, stderr
from traceback import print_exception

import weakref

# Import the low-level C/C++ module.
if getattr(globals().get("__spec__"), "parent", None) or __package__ or "." in __name__:
    from . import _ctp
else:
    import _ctp

try:
    import builtins as __builtin__
except ImportError:
    import __builtin__


def _swig_repr(self):
    try:
        strthis = "proxy of " + self.this.__repr__()
    except __builtin__.Exception:
        strthis = ""
    return f"<{self.__class__.__module__}.{self.__class__.__name__}; {strthis} >"


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
