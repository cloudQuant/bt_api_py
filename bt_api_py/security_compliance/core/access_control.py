"""Access Control Management for Zero Trust Architecture.

Implements role-based access control (RBAC) with attribute-based access control (ABAC)
capabilities for financial industry compliance with zero trust principles.
"""

import enum
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Union
from uuid import uuid4

from bt_api_py.exceptions import BtApiError


class AccessDeniedError(BtApiError):
    """Access denied due to insufficient permissions."""

    def __init__(self, user_id: str, resource: str, action: str, reason: str = ""):
        msg = f"Access denied for user {user_id} to {action} {resource}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.user_id = user_id
        self.resource = resource
        self.action = action
        self.reason = reason


class PermissionLevel(enum.IntEnum):
    """Permission levels for hierarchical access control."""

    NONE = 0
    READ = 1
    WRITE = 2
    DELETE = 4
    ADMIN = 8
    SUPER_ADMIN = 16


class Resource(enum.Enum):
    """Protected resources in the trading system."""

    EXCHANGE_CONFIG = "exchange_config"
    TRADING_ACCOUNTS = "trading_accounts"
    ORDER_MANAGEMENT = "order_management"
    MARKET_DATA = "market_data"
    USER_ACCOUNTS = "user_accounts"
    AUDIT_LOGS = "audit_logs"
    SECURITY_CONFIG = "security_config"
    API_KEYS = "api_keys"
    BALANCE_DATA = "balance_data"
    POSITION_DATA = "position_data"
    TRADE_HISTORY = "trade_history"


@dataclass(frozen=True)
class Permission:
    """Represents a permission with resource, action, and level."""

    resource: Resource
    action: str
    level: PermissionLevel

    def __str__(self) -> str:
        return f"{self.resource.value}:{self.action}:{self.level.name}"


@dataclass
class Role:
    """Role with associated permissions and attributes."""

    name: str
    description: str
    permissions: Set[Permission] = field(default_factory=set)
    attributes: Dict[str, Any] = field(default_factory=dict)
    is_system_role: bool = False
    created_at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: str(uuid4()))

    def add_permission(self, permission: Permission) -> None:
        """Add a permission to this role."""
        self.permissions.add(permission)

    def remove_permission(self, permission: Permission) -> None:
        """Remove a permission from this role."""
        self.permissions.discard(permission)

    def has_permission(self, resource: Resource, action: str, level: PermissionLevel) -> bool:
        """Check if role has the required permission."""
        for perm in self.permissions:
            action_match = perm.action == "*" or perm.action == action
            if not action_match and perm.action == "write":
                action_match = action in {"create", "update", "modify", "write"}
            if not action_match and perm.action == "read":
                action_match = action in {"read", "view", "list"}

            if perm.resource == resource and action_match:
                return perm.level >= level
        return False


@dataclass
class User:
    """User entity with roles and attributes for ABAC."""

    user_id: str
    username: str
    email: str
    roles: Set[str] = field(default_factory=set)
    attributes: Dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    created_at: float = field(default_factory=time.time)
    last_login: Optional[float] = None
    failed_login_attempts: int = 0
    locked_until: Optional[float] = None

    def add_role(self, role_name: str) -> None:
        """Add a role to the user."""
        self.roles.add(role_name)

    def remove_role(self, role_name: str) -> None:
        """Remove a role from the user."""
        self.roles.discard(role_name)

    def has_role(self, role_name: str) -> bool:
        """Check if user has a specific role."""
        return role_name in self.roles

    def is_locked(self) -> bool:
        """Check if user account is locked."""
        if self.locked_until is None:
            return False
        return time.time() < self.locked_until


@dataclass
class AccessContext:
    """Context for access decisions (ABAC attributes)."""

    user: User
    resource: Resource
    action: str
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    session_id: Optional[str] = None
    request_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert context to dictionary for evaluation."""
        return {
            "user_id": self.user.user_id,
            "username": self.user.username,
            "resource": self.resource.value,
            "action": self.action,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "request_id": self.request_id,
            **self.user.attributes,
        }


class AccessControlManager:
    """Zero Trust Access Control Manager with RBAC + ABAC."""

    def __init__(self, encryption_key: Optional[str] = None):
        """Initialize access control manager."""
        self._users: Dict[str, User] = {}
        self._roles: Dict[str, Role] = {}
        self._session_contexts: Dict[str, AccessContext] = {}
        self._encryption_key = encryption_key

        # Initialize system roles
        self._init_system_roles()

    def _init_system_roles(self) -> None:
        """Initialize default system roles."""
        # Super Admin - full access
        super_admin = Role(
            name="super_admin", description="Full system access", is_system_role=True
        )

        # Grant all permissions to super admin
        for resource in Resource:
            super_admin.add_permission(
                Permission(resource=resource, action="*", level=PermissionLevel.SUPER_ADMIN)
            )

        self._roles["super_admin"] = super_admin

        # Trader - trading permissions only
        trader = Role(name="trader", description="Trading access only", is_system_role=True)

        trading_resources = [
            Resource.MARKET_DATA,
            Resource.ORDER_MANAGEMENT,
            Resource.BALANCE_DATA,
            Resource.POSITION_DATA,
            Resource.TRADE_HISTORY,
        ]

        for resource in trading_resources:
            for action in ["read", "write"]:
                trader.add_permission(
                    Permission(resource=resource, action=action, level=PermissionLevel.WRITE)
                )

        self._roles["trader"] = trader

        # Auditor - read-only access to audit logs
        auditor = Role(
            name="auditor", description="Audit and compliance access", is_system_role=True
        )

        audit_resources = [
            Resource.AUDIT_LOGS,
            Resource.TRADE_HISTORY,
            Resource.ORDER_MANAGEMENT,
        ]

        for resource in audit_resources:
            auditor.add_permission(
                Permission(resource=resource, action="read", level=PermissionLevel.READ)
            )

        self._roles["auditor"] = auditor

    def create_user(self, user_id: str, username: str, email: str, **attributes) -> User:
        """Create a new user."""
        if user_id in self._users:
            raise ValueError(f"User {user_id} already exists")

        user = User(user_id=user_id, username=username, email=email, attributes=attributes)
        user.add_role("trader")

        self._users[user_id] = user
        return user

    def get_user(self, user_id: str) -> Optional[User]:
        """Get user by ID."""
        return self._users.get(user_id)

    def create_role(self, name: str, description: str, **attributes) -> Role:
        """Create a new role."""
        if name in self._roles:
            raise ValueError(f"Role {name} already exists")

        role = Role(name=name, description=description, attributes=attributes)
        self._roles[name] = role
        return role

    def get_role(self, name: str) -> Optional[Role]:
        """Get role by name."""
        return self._roles.get(name)

    def assign_role(self, user_id: str, role_name: str) -> None:
        """Assign a role to a user."""
        user = self.get_user(user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")

        role = self.get_role(role_name)
        if not role:
            raise ValueError(f"Role {role_name} not found")

        user.add_role(role_name)

    def revoke_role(self, user_id: str, role_name: str) -> None:
        """Revoke a role from a user."""
        user = self.get_user(user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")

        user.remove_role(role_name)

    def check_permission(
        self,
        user_id: str,
        resource: Resource,
        action: str,
        level: PermissionLevel,
        context: Optional[AccessContext] = None,
    ) -> bool:
        """Check if user has permission (Zero Trust verification)."""
        user = self.get_user(user_id)
        if not user:
            return False

        if not user.is_active or user.is_locked():
            return False

        # Check role-based permissions
        for role_name in user.roles:
            role = self.get_role(role_name)
            if role and role.has_permission(resource, action, level):
                return True

        # Check attribute-based permissions
        if context and self._check_abac_rules(user, resource, action, level, context):
            return True

        return False

    def _check_abac_rules(
        self,
        user: User,
        resource: Resource,
        action: str,
        level: PermissionLevel,
        context: AccessContext,
    ) -> bool:
        """Check attribute-based access control rules."""
        # Example ABAC rules

        # Rule 1: Users can only access their own trading accounts during business hours
        if resource == Resource.TRADING_ACCOUNTS:
            if "account_owner" in user.attributes:
                import datetime

                now = datetime.datetime.fromtimestamp(context.timestamp)
                if 9 <= now.hour <= 17:  # Business hours
                    return True

        # Rule 2: IP-based restrictions for sensitive operations
        if resource in [Resource.API_KEYS, Resource.SECURITY_CONFIG]:
            if "allowed_ips" in user.attributes:
                if context.ip_address not in user.attributes["allowed_ips"]:
                    return False

        # Rule 3: Time-based restrictions for admin operations
        if level >= PermissionLevel.ADMIN:
            import datetime

            now = datetime.datetime.fromtimestamp(context.timestamp)
            if now.weekday() >= 5:  # Weekend restriction
                return False

        return False

    def require_permission(
        self,
        user_id: str,
        resource: Resource,
        action: str,
        level: PermissionLevel,
        context: Optional[AccessContext] = None,
    ) -> None:
        """Require permission or raise AccessDeniedError."""
        if not self.check_permission(user_id, resource, action, level, context):
            reason = "Insufficient permissions"
            if context and context.user.is_locked():
                reason = "Account is locked"
            raise AccessDeniedError(user_id, resource.value, action, reason)

    def create_session(self, user_id: str, **context_attrs) -> str:
        """Create an access session."""
        user = self.get_user(user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")

        if not user.is_active or user.is_locked():
            raise AccessDeniedError(user_id, "session", "create", "Account inactive or locked")

        session_id = str(uuid4())
        context = AccessContext(
            user=user, resource=Resource.USER_ACCOUNTS, action="create_session", **context_attrs
        )

        self._session_contexts[session_id] = context
        user.last_login = time.time()
        user.failed_login_attempts = 0

        return session_id

    def validate_session(self, session_id: str) -> Optional[AccessContext]:
        """Validate a session and return context."""
        return self._session_contexts.get(session_id)

    def revoke_session(self, session_id: str) -> None:
        """Revoke a session."""
        self._session_contexts.pop(session_id, None)

    def lock_user(self, user_id: str, duration: int = 900) -> None:
        """Lock a user account for specified duration."""
        user = self.get_user(user_id)
        if user:
            user.locked_until = time.time() + duration

    def unlock_user(self, user_id: str) -> None:
        """Unlock a user account."""
        user = self.get_user(user_id)
        if user:
            user.locked_until = None
            user.failed_login_attempts = 0

    def record_login_attempt(self, user_id: str, success: bool) -> None:
        """Record a login attempt."""
        user = self.get_user(user_id)
        if not user:
            return

        if success:
            user.failed_login_attempts = 0
        else:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= 3:
                self.lock_user(user_id, 900)  # 15 minutes

    def get_user_permissions(self, user_id: str) -> Set[Permission]:
        """Get all permissions for a user."""
        user = self.get_user(user_id)
        if not user:
            return set()

        permissions = set()
        for role_name in user.roles:
            role = self.get_role(role_name)
            if role:
                permissions.update(role.permissions)

        return permissions

    def audit_access(
        self,
        user_id: str,
        resource: Resource,
        action: str,
        granted: bool,
        context: Optional[AccessContext] = None,
    ) -> Dict[str, Any]:
        """Create an audit record for access attempt."""
        return {
            "user_id": user_id,
            "resource": resource.value,
            "action": action,
            "granted": granted,
            "timestamp": time.time(),
            "ip_address": context.ip_address if context else None,
            "user_agent": context.user_agent if context else None,
            "session_id": context.session_id if context else None,
            "request_id": context.request_id if context else None,
        }
