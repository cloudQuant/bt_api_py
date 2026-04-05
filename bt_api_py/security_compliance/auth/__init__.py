"""Placeholder modules for authentication components."""

from __future__ import annotations

from .mfa_provider import MFAProvider
from .oauth2_provider import OAuth2Provider

__all__ = ["OAuth2Provider", "MFAProvider"]
