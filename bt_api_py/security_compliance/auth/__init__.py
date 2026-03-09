"""Placeholder modules for authentication components."""

from .mfa_provider import MFAProvider
from .oauth2_provider import OAuth2Provider

__all__ = ["OAuth2Provider", "MFAProvider"]
