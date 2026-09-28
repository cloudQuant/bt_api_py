"""Explicit Iteration 41 managed-runtime capability composition.

This package never discovers entry points, loads exchange adapters, or reads a
user configuration file.  A caller supplies an already-sealed effective
runtime contract and a code-owned catalog of exact capability distributions.
Optional capabilities are imported only after those two checks pass.
"""

from .cancellation_control import (
    CancellationControlAuditConflictError,
    CancellationControlAuditError,
    CancellationControlCommandAudit,
    CancellationFreezeReleaseResult,
    CancellationReconciliationAudit,
    CancellationReconciliationEvidence,
    CancellationReleaseAuthorizationRequest,
    ControlledCancellationReconciliationResult,
    DurableCancellationControlAudit,
    ManagedCancellationReconciliationControlPort,
    ReleaseCancellationFreezeCommand,
)
from .catalog import (
    CapabilityCatalog,
    CapabilityPin,
    LoadedCapabilities,
    RuntimePluginError,
)
from .contracts import (
    CAPABILITY_EXECUTION,
    CAPABILITY_GATEWAY,
    CAPABILITY_MONITOR,
    CAPABILITY_RISK,
    CAPABILITY_TRANSPORT_ZMQ,
    RuntimeCapabilityContract,
)
from .gateway_dispatch import (
    GatewayExecutionAuthority,
    GatewayManagedDispatcher,
    GatewayManagedDispatchError,
    GatewayManagedExecutionRuntime,
    GatewayManagedOutcomeUnknown,
    GatewayManagedOutcomeUnknownError,
    compose_gateway_execution_authority,
    compose_gateway_managed_client,
    gateway_command_id,
)
from .instrument_risk import (
    InstrumentRiskAdmission,
    NormalizedInstrumentMetadata,
    SealedNormalizedInstrumentMetadataSnapshot,
    compose_instrument_risk_admission,
)
from .managed import ManagedExecutionRuntime, compose_managed_execution
from .managed_recovery import (
    DurableManagedRecoveryCoordinator,
    ManagedRecoveryCoordinatorError,
    ManagedRecoveryEvent,
    ManagedRecoveryReport,
    ManagedRecoveryWork,
)
from .reconcile_control import (
    AuthorizationDecision,
    ControlAuditConflictError,
    ControlAuditError,
    ControlCommandAudit,
    ControlCommandStatus,
    ControlledReconciliationResult,
    DurableReconciliationControlAudit,
    FreezeReleaseResult,
    ManagedReconciliationControlPort,
    ReconciliationAudit,
    ReconciliationEvidence,
    ReleaseAuthorizationRequest,
    ReleaseIntentFreezeCommand,
)

__all__ = [
    "CAPABILITY_EXECUTION",
    "CAPABILITY_GATEWAY",
    "CAPABILITY_MONITOR",
    "CAPABILITY_RISK",
    "CAPABILITY_TRANSPORT_ZMQ",
    "AuthorizationDecision",
    "CancellationControlAuditConflictError",
    "CancellationControlAuditError",
    "CancellationControlCommandAudit",
    "CancellationFreezeReleaseResult",
    "CancellationReconciliationAudit",
    "CancellationReconciliationEvidence",
    "CancellationReleaseAuthorizationRequest",
    "CapabilityCatalog",
    "CapabilityPin",
    "ControlAuditConflictError",
    "ControlAuditError",
    "ControlCommandAudit",
    "ControlCommandStatus",
    "ControlledCancellationReconciliationResult",
    "ControlledReconciliationResult",
    "DurableReconciliationControlAudit",
    "DurableCancellationControlAudit",
    "DurableManagedRecoveryCoordinator",
    "FreezeReleaseResult",
    "GatewayExecutionAuthority",
    "GatewayManagedDispatchError",
    "GatewayManagedDispatcher",
    "GatewayManagedExecutionRuntime",
    "GatewayManagedOutcomeUnknown",
    "GatewayManagedOutcomeUnknownError",
    "InstrumentRiskAdmission",
    "NormalizedInstrumentMetadata",
    "LoadedCapabilities",
    "ManagedReconciliationControlPort",
    "ManagedCancellationReconciliationControlPort",
    "ManagedExecutionRuntime",
    "ManagedRecoveryCoordinatorError",
    "ManagedRecoveryEvent",
    "ManagedRecoveryReport",
    "ManagedRecoveryWork",
    "ReconciliationAudit",
    "ReconciliationEvidence",
    "ReleaseAuthorizationRequest",
    "ReleaseIntentFreezeCommand",
    "ReleaseCancellationFreezeCommand",
    "RuntimeCapabilityContract",
    "RuntimePluginError",
    "SealedNormalizedInstrumentMetadataSnapshot",
    "compose_managed_execution",
    "compose_gateway_execution_authority",
    "compose_gateway_managed_client",
    "compose_instrument_risk_admission",
    "gateway_command_id",
]
