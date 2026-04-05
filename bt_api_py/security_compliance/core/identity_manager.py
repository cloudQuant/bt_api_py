"""Identity and access management with SSO integration.

Centralized identity management supporting LDAP, SAML, OpenID Connect,
and single sign-on for enterprise environments.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

try:
    import bcrypt

    BCRYPT_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    bcrypt = None  # type: ignore[assignment]
    BCRYPT_AVAILABLE = False


class IdentityProvider(Enum):
    """Identity provider types."""

    LOCAL = "local"
    LDAP = "ldap"
    SAML = "saml"
    OPENID_CONNECT = "openid_connect"
    ACTIVE_DIRECTORY = "active_directory"


class UserStatus(Enum):
    """User account status."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    LOCKED = "locked"


@dataclass
class Identity:
    """User identity information."""

    identity_id: str
    username: str
    email: str
    full_name: str
    department: str | None
    title: str | None
    manager_id: str | None
    groups: set[str] = field(default_factory=set)
    attributes: dict[str, Any] = field(default_factory=dict)
    provider: IdentityProvider = IdentityProvider.LOCAL
    status: UserStatus = UserStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    last_login: float | None = None
    password_changed: float | None = None


@dataclass
class Group:
    """User group for role-based access."""

    group_id: str
    name: str
    description: str
    members: set[str] = field(default_factory=set)
    parent_groups: set[str] = field(default_factory=set)
    attributes: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class IdentityManager:
    """Identity and access management system."""

    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize identity manager."""
        self.config = config or {}

        # Storage
        self._identities: dict[str, Identity] = {}
        self._groups: dict[str, Group] = {}
        self._user_groups: dict[str, set[str]] = {}

        # Provider configurations
        self._providers: dict[IdentityProvider, dict[str, Any]] = {}
        self._init_default_providers()

        # Default groups
        self._init_default_groups()

    def _init_default_providers(self):
        """Initialize default identity providers."""
        # Local provider always available
        self._providers[IdentityProvider.LOCAL] = {"enabled": True, "config": {}}

        # LDAP provider if configured
        if self.config.get("ldap"):
            self._providers[IdentityProvider.LDAP] = {
                "enabled": True,
                "config": self.config["ldap"],
            }

        # SAML provider if configured
        if self.config.get("saml"):
            self._providers[IdentityProvider.SAML] = {
                "enabled": True,
                "config": self.config["saml"],
            }

    def _init_default_groups(self):
        """Initialize default user groups."""
        # Traders group
        traders = Group(group_id="traders", name="Traders", description="Trading desk users")
        self._groups["traders"] = traders

        # Administrators group
        admins = Group(
            group_id="administrators", name="Administrators", description="System administrators"
        )
        self._groups["administrators"] = admins

        # Auditors group
        auditors = Group(
            group_id="auditors", name="Auditors", description="Compliance and audit users"
        )
        self._groups["auditors"] = auditors

    def create_identity(
        self,
        username: str,
        email: str,
        full_name: str,
        password: str | None = None,
        provider: IdentityProvider = IdentityProvider.LOCAL,
        **attributes,
    ) -> Identity:
        """Create a new user identity."""
        identity_id = str(uuid4())

        identity = Identity(
            identity_id=identity_id,
            username=username,
            email=email,
            full_name=full_name,
            department=None,
            title=None,
            manager_id=None,
            provider=provider,
            attributes=dict(attributes),
        )

        self._identities[identity_id] = identity
        self._user_groups[identity_id] = set()

        # Store password hash for local provider
        if provider == IdentityProvider.LOCAL and password:
            identity.attributes["password_hash"] = self._hash_password(password)
            identity.password_changed = time.time()

        return identity

    def _hash_password(self, password: str) -> str:
        """Hash password securely."""
        if not BCRYPT_AVAILABLE:
            raise ImportError(
                "bcrypt is required for password hashing. Install with: pip install bcrypt"
            )

        # Use bcrypt for secure password hashing
        salt = bcrypt.gensalt()
        password_hash = bcrypt.hashpw(password.encode("utf-8"), salt)
        return password_hash.decode("utf-8")

    def verify_password(self, identity_id: str, password: str) -> bool:
        """Verify user password."""
        identity = self._identities.get(identity_id)
        if not identity or identity.provider != IdentityProvider.LOCAL:
            return False

        password_hash = identity.attributes.get("password_hash")
        if not password_hash:
            return False

        if not BCRYPT_AVAILABLE:
            raise ImportError(
                "bcrypt is required for password verification. Install with: pip install bcrypt"
            )

        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))

    def authenticate_user(
        self, username: str, password: str, provider: IdentityProvider = IdentityProvider.LOCAL
    ) -> Identity | None:
        """Authenticate user credentials."""
        # Find identity by username and provider
        identity = None
        for ident in self._identities.values():
            if ident.username == username and ident.provider == provider:
                identity = ident
                break

        if not identity:
            return None

        # Check account status
        if identity.status != UserStatus.ACTIVE:
            return None

        # Verify credentials based on provider
        if provider == IdentityProvider.LOCAL:
            if self.verify_password(identity.identity_id, password):
                identity.last_login = time.time()
                return identity
        elif provider == IdentityProvider.LDAP:
            # LDAP authentication would go here
            if self._authenticate_ldap(username, password):
                identity.last_login = time.time()
                return identity
        elif provider == IdentityProvider.SAML:
            # SAML authentication uses different flow
            pass

        return None

    def _authenticate_ldap(self, username: str, password: str) -> bool:
        """Authenticate against LDAP server."""
        # Placeholder for LDAP authentication
        # In real implementation, would use python-ldap or similar
        ldap_config = self._providers.get(IdentityProvider.LDAP, {}).get("config", {})

        # Mock authentication for demo
        return username in ldap_config.get("users", {})

    def create_group(
        self, name: str, description: str, parent_group_ids: set[str] | None = None
    ) -> Group:
        """Create a new user group."""
        group_id = str(uuid4())

        group = Group(
            group_id=group_id,
            name=name,
            description=description,
            parent_groups=parent_group_ids or set(),
        )

        self._groups[group_id] = group
        return group

    def add_user_to_group(self, identity_id: str, group_id: str) -> bool:
        """Add user to a group."""
        identity = self._identities.get(identity_id)
        group = self._groups.get(group_id)

        if not identity or not group:
            return False

        group.members.add(identity_id)
        self._user_groups[identity_id].add(group_id)
        return True

    def remove_user_from_group(self, identity_id: str, group_id: str) -> bool:
        """Remove user from a group."""
        identity = self._identities.get(identity_id)
        group = self._groups.get(group_id)

        if not identity or not group:
            return False

        group.members.discard(identity_id)
        self._user_groups[identity_id].discard(group_id)
        return True

    def get_user_groups(self, identity_id: str) -> set[str]:
        """Get all groups for a user."""
        return self._user_groups.get(identity_id, set())

    def get_group_members(self, group_id: str) -> set[str]:
        """Get all members of a group."""
        group = self._groups.get(group_id)
        return group.members if group else set()

    def update_identity(self, identity_id: str, **updates) -> bool:
        """Update identity information."""
        identity = self._identities.get(identity_id)
        if not identity:
            return False

        for key, value in updates.items():
            if hasattr(identity, key):
                setattr(identity, key, value)
            elif key in identity.attributes:
                identity.attributes[key] = value
            else:
                identity.attributes[key] = value

        return True

    def suspend_user(self, identity_id: str, reason: str) -> bool:
        """Suspend user account."""
        identity = self._identities.get(identity_id)
        if not identity:
            return False

        identity.status = UserStatus.SUSPENDED
        identity.attributes["suspension_reason"] = reason
        identity.attributes["suspended_at"] = time.time()

        return True

    def activate_user(self, identity_id: str) -> bool:
        """Activate user account."""
        identity = self._identities.get(identity_id)
        if not identity:
            return False

        identity.status = UserStatus.ACTIVE
        identity.attributes.pop("suspension_reason", None)
        identity.attributes.pop("suspended_at", None)

        return True

    def lock_user(self, identity_id: str, reason: str) -> bool:
        """Lock user account."""
        identity = self._identities.get(identity_id)
        if not identity:
            return False

        identity.status = UserStatus.LOCKED
        identity.attributes["lock_reason"] = reason
        identity.attributes["locked_at"] = time.time()

        return True

    def search_identities(
        self,
        query: str | None = None,
        group_id: str | None = None,
        department: str | None = None,
        status: UserStatus | None = None,
        limit: int = 100,
    ) -> list[Identity]:
        """Search identities with filters."""
        results = []

        for identity in self._identities.values():
            # Apply filters
            if query and (
                query.lower() not in identity.username.lower()
                and query.lower() not in identity.email.lower()
                and query.lower() not in identity.full_name.lower()
            ):
                continue

            if group_id and group_id not in self.get_user_groups(identity.identity_id):
                continue

            if department and identity.department != department:
                continue

            if status and identity.status != status:
                continue

            results.append(identity)

            if len(results) >= limit:
                break

        return results

    def get_identity_by_username(self, username: str) -> Identity | None:
        """Get identity by username."""
        for identity in self._identities.values():
            if identity.username == username:
                return identity
        return None

    def get_identity(self, identity_id: str) -> Identity | None:
        """Get identity by ID."""
        return self._identities.get(identity_id)

    def get_group(self, group_id: str) -> Group | None:
        """Get group by ID."""
        return self._groups.get(group_id)

    def get_user_permissions(self, identity_id: str) -> dict[str, Any]:
        """Get all permissions for a user through group memberships."""
        user_groups = self.get_user_groups(identity_id)
        direct_permissions: set[str] = set()
        permissions: dict[str, Any] = {
            "groups": list(user_groups),
            "direct_permissions": direct_permissions,
            "inherited_permissions": set(),
        }

        # Collect permissions from groups
        for group_id in user_groups:
            group = self._groups.get(group_id)
            if group and "permissions" in group.attributes:
                perms = group.attributes["permissions"]
                if isinstance(perms, (set, list)):
                    direct_permissions.update(str(p) for p in perms)

        # In a real implementation, this would resolve permission hierarchies
        return permissions

    def sync_with_external_provider(self, provider: IdentityProvider) -> dict[str, Any]:
        """Synchronize identities with external provider."""
        sync_results: dict[str, Any] = {
            "provider": provider.value,
            "created": 0,
            "updated": 0,
            "errors": [],
        }

        if provider == IdentityProvider.LDAP:
            sync_results.update(self._sync_ldap())
        elif provider == IdentityProvider.SAML:
            sync_results.update(self._sync_saml())

        return sync_results

    def _sync_ldap(self) -> dict[str, Any]:
        """Synchronize with LDAP provider."""
        # Placeholder for LDAP synchronization
        # In real implementation, would query LDAP and create/update identities
        return {"created": 0, "updated": 0, "errors": []}

    def _sync_saml(self) -> dict[str, Any]:
        """Synchronize with SAML provider."""
        # Placeholder for SAML synchronization
        return {"created": 0, "updated": 0, "errors": []}

    def generate_saml_metadata(self) -> str:
        """Generate SAML metadata for identity provider."""
        # Placeholder for SAML metadata generation
        # In real implementation, would generate proper XML metadata
        return """<?xml version="1.0" encoding="UTF-8"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata">
    <md:IDPSSODescriptor>
        <!-- SAML metadata would go here -->
    </md:IDPSSODescriptor>
</md:EntityDescriptor>"""

    def get_identity_summary(self) -> dict[str, Any]:
        """Get summary of identity management statistics."""
        total_identities = len(self._identities)
        status_counts: dict[str, int] = {}
        provider_counts: dict[str, int] = {}

        for identity in self._identities.values():
            # Count by status
            status = identity.status.value
            status_counts[status] = status_counts.get(status, 0) + 1

            # Count by provider
            provider = identity.provider.value
            provider_counts[provider] = provider_counts.get(provider, 0) + 1

        return {
            "total_identities": total_identities,
            "total_groups": len(self._groups),
            "by_status": status_counts,
            "by_provider": provider_counts,
            "providers": list(self._providers.keys()),
        }
