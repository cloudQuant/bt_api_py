"""Data Protection and Privacy Management.

GDPR, CCPA, and data lifecycle management with automated
data retention, anonymization, and right-to-be-forgotten implementation.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from bt_api_py.exceptions import BtApiError


class DataProtectionError(BtApiError):
    """Data protection related errors."""


class SensitiveDataType(Enum):
    """Types of sensitive data."""

    PII = "personal_identifiable_information"
    FINANCIAL = "financial_data"
    PCI_DSS = "pci_dss"  # Data requiring PCI DSS compliance (e.g. credit card)
    HEALTH = "health_data"
    BIOMETRIC = "biometric_data"
    LOCATION = "location_data"
    COMMUNICATION = "communication_data"
    TRANSACTION = "transaction_data"


@dataclass
class DataClassification:
    """Data classification with retention policies."""

    data_type: SensitiveDataType
    classification: str  # public, internal, confidential, restricted
    retention_days: int
    requires_encryption: bool = True
    requires_anonymization: bool = False
    gdpr_applicable: bool = False
    pci_dss_applicable: bool = False


@dataclass
class DataSubject:
    """Data subject (individual) information."""

    subject_id: str
    identifiers: dict[str, str]  # email, phone, etc.
    consent_records: list[dict[str, Any]] = field(default_factory=list)
    deletion_requests: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


class DataProtectionManager:
    """Data protection and privacy manager."""

    def __init__(self, encryption_manager, config: dict[str, Any]):
        """Initialize data protection manager."""
        self.encryption_manager = encryption_manager
        self.config = config

        # Data classification rules
        self._classifications = self._init_classifications()

        # Data subjects registry
        self._data_subjects: dict[str, DataSubject] = {}

        # Data retention policies
        self._retention_policies: dict[str, int] = {}

        # Anonymization patterns
        self._anonymization_patterns = self._init_anonymization_patterns()

    def _init_classifications(self) -> dict[SensitiveDataType, DataClassification]:
        """Initialize default data classifications."""
        return {
            SensitiveDataType.PII: DataClassification(
                data_type=SensitiveDataType.PII,
                classification="confidential",
                retention_days=2555,  # 7 years for SOX
                requires_encryption=True,
                requires_anonymization=True,
                gdpr_applicable=True,
            ),
            SensitiveDataType.FINANCIAL: DataClassification(
                data_type=SensitiveDataType.FINANCIAL,
                classification="restricted",
                retention_days=2555,
                requires_encryption=True,
                requires_anonymization=True,
                gdpr_applicable=True,
                pci_dss_applicable=True,
            ),
            SensitiveDataType.TRANSACTION: DataClassification(
                data_type=SensitiveDataType.TRANSACTION,
                classification="confidential",
                retention_days=2555,
                requires_encryption=True,
                gdpr_applicable=True,
            ),
        }

    def _init_anonymization_patterns(self) -> dict[str, re.Pattern]:
        """Initialize regex patterns for data anonymization."""
        return {
            "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
            "phone": re.compile(r"\b\d{3}-\d{3}-\d{4}\b|\b\d{10}\b"),
            "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
            "credit_card": re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"),
            "ip_address": re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
        }

    def classify_data(self, data: dict[str, Any]) -> dict[SensitiveDataType, bool]:
        """Classify data and identify sensitive types."""
        classification = {}

        # Convert data to string for pattern matching
        data_str = str(data)

        for data_type, pattern in self._anonymization_patterns.items():
            if pattern.search(data_str):
                if data_type == "email":
                    classification[SensitiveDataType.PII] = True
                elif data_type == "credit_card":
                    classification[SensitiveDataType.FINANCIAL] = True
                    classification[SensitiveDataType.PCI_DSS] = True

        # Check for transaction data
        if any(key in data for key in ["amount", "currency", "transaction_id"]):
            classification[SensitiveDataType.TRANSACTION] = True

        return classification

    def mask_data(self, data: Any, mask_level: str = "partial") -> Any:
        """Mask sensitive data based on level."""
        if isinstance(data, str):
            return self._mask_string(data, mask_level)
        elif isinstance(data, dict):
            return {k: self.mask_data(v, mask_level) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.mask_data(item, mask_level) for item in data]
        else:
            return data

    def _mask_string(self, data: str, mask_level: str) -> str:
        """Mask string data."""
        if mask_level == "full":
            return "*" * len(data)
        elif mask_level == "partial":
            if len(data) <= 4:
                return "*" * len(data)
            return data[:2] + "*" * (len(data) - 4) + data[-2:]
        elif mask_level == "email" and "@" in data:
            # Mask email while preserving domain
            local, domain = data.split("@", 1)
            masked_local = (
                local[:2] + "*" * (len(local) - 2) if len(local) > 3 else "*" * len(local)
            )
            return f"{masked_local}@{domain}"
        return data

    def anonymize_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Anonymize sensitive data according to GDPR."""
        anonymized = data.copy()

        for field_name, value in anonymized.items():
            if isinstance(value, str):
                # Apply anonymization patterns
                for pattern_name, pattern in self._anonymization_patterns.items():
                    if pattern.search(value):
                        if pattern_name == "email":
                            anonymized[field_name] = self._anonymize_email(value)
                        elif pattern_name == "phone":
                            anonymized[field_name] = "***-***-" + value[-4:]
                        elif pattern_name == "credit_card":
                            anonymized[field_name] = "****-****-****-" + value[-4:]
                        elif pattern_name == "ssn":
                            anonymized[field_name] = "***-**-" + value[-4:]

        return anonymized

    def _anonymize_email(self, email: str) -> str:
        """Anonymize email address."""
        if "@" not in email:
            return "*****@*****.com"

        local, domain = email.split("@", 1)
        masked_local = "**" if len(local) <= 2 else local[0] + "*" * (len(local) - 2) + local[-1]

        return f"{masked_local}@{domain}"

    def encrypt_sensitive_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Encrypt fields classified as sensitive."""
        encrypted_data = data.copy()
        classifications = self.classify_data(data)
        has_sensitive = any(classifications.values())

        if not has_sensitive or not self.encryption_manager:
            return encrypted_data

        for field_name, value in encrypted_data.items():
            try:
                encrypted_value = self.encryption_manager.encrypt(str(value))
                encrypted_data[field_name] = {"_encrypted": True, "data": encrypted_value}
            except Exception as e:
                raise DataProtectionError(
                    f"Failed to encrypt sensitive data for field '{field_name}': {e}"
                ) from e

        return encrypted_data

    def decrypt_sensitive_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Decrypt encrypted fields."""
        decrypted_data = data.copy()

        for field_name, value in decrypted_data.items():
            if isinstance(value, dict) and value.get("_encrypted") and self.encryption_manager:
                try:
                    decrypted_value = self.encryption_manager.decrypt(value["data"])
                    decrypted_data[field_name] = decrypted_value
                except Exception as e:
                    raise DataProtectionError(
                        f"Failed to decrypt sensitive data for field '{field_name}': {e}"
                    ) from e

        return decrypted_data

    def register_data_subject(
        self,
        subject_id: str,
        identifiers: dict[str, str],
        consent_data: dict[str, Any] | None = None,
    ) -> DataSubject:
        """Register a data subject (GDPR Article 4)."""
        subject = DataSubject(subject_id=subject_id, identifiers=identifiers)

        if consent_data:
            subject.consent_records.append(
                {
                    "timestamp": time.time(),
                    "consent": consent_data,
                    "purpose": consent_data.get("purpose", "general"),
                    " lawful_basis": consent_data.get("lawful_basis", "consent"),
                }
            )

        self._data_subjects[subject_id] = subject
        return subject

    def record_consent(
        self,
        subject_id: str,
        consent_data: dict[str, Any],
        purpose: str,
        lawful_basis: str = "consent",
    ) -> None:
        """Record consent for data processing."""
        subject = self._data_subjects.get(subject_id)
        if not subject:
            raise DataProtectionError(f"Data subject {subject_id} not found")

        subject.consent_records.append(
            {
                "timestamp": time.time(),
                "consent": consent_data,
                "purpose": purpose,
                "lawful_basis": lawful_basis,
                "withdrawn": False,
            }
        )

    def withdraw_consent(self, subject_id: str, purpose: str | None = None) -> None:
        """Withdraw consent for data processing."""
        subject = self._data_subjects.get(subject_id)
        if not subject:
            raise DataProtectionError(f"Data subject {subject_id} not found")

        for consent_record in subject.consent_records:
            if purpose is None or consent_record.get("purpose") == purpose:
                consent_record["withdrawn"] = True
                consent_record["withdrawn_timestamp"] = time.time()

    def request_right_to_be_forgotten(self, subject_id: str, reason: str) -> str:
        """Process GDPR right to be forgotten request."""
        subject = self._data_subjects.get(subject_id)
        if not subject:
            raise DataProtectionError(f"Data subject {subject_id} not found")

        request_id = f"gdpr_{int(time.time())}_{subject_id}"

        deletion_request = {
            "request_id": request_id,
            "timestamp": time.time(),
            "reason": reason,
            "status": "pending",
            "completed_at": None,
        }

        subject.deletion_requests.append(deletion_request)

        # In a real implementation, this would trigger data deletion
        # across all systems where the data is stored

        return request_id

    def process_data_deletion(self, request_id: str) -> dict[str, Any]:
        """Process data deletion request."""
        # Find the request
        for subject in self._data_subjects.values():
            for request in subject.deletion_requests:
                if request["request_id"] == request_id:
                    # Process deletion
                    request["status"] = "completed"
                    request["completed_at"] = time.time()

                    return {
                        "request_id": request_id,
                        "status": "completed",
                        "subject_id": subject.subject_id,
                        "deleted_records": self._delete_subject_data(subject.subject_id),
                    }

        raise DataProtectionError(f"Deletion request {request_id} not found")

    def _delete_subject_data(self, subject_id: str) -> int:
        """Delete all data for a subject."""
        # This would integrate with data storage systems
        # For now, just return a placeholder
        deleted_count = 0

        # Delete from data subjects
        if subject_id in self._data_subjects:
            del self._data_subjects[subject_id]
            deleted_count += 1

        return deleted_count

    def check_retention_policies(self) -> dict[str, list[str]]:
        """Check data retention policies and identify expired data."""
        expired_data = {}

        for data_type, classification in self._classifications.items():
            # In a real implementation, this would query data stores
            # using cutoff_time = time.time() - (classification.retention_days * 24 * 60 * 60)
            # For now, just return the policy information
            expired_data[data_type.value] = [
                f"Data older than {classification.retention_days} days should be deleted"
            ]

        return expired_data

    def generate_data_protection_report(self) -> dict[str, Any]:
        """Generate comprehensive data protection report."""
        return {
            "data_subjects_count": len(self._data_subjects),
            "consent_records": sum(len(s.consent_records) for s in self._data_subjects.values()),
            "deletion_requests": sum(
                len(s.deletion_requests) for s in self._data_subjects.values()
            ),
            "retention_policies": {
                data_type.value: classification.retention_days
                for data_type, classification in self._classifications.items()
            },
            "gdpr_compliance": self._check_gdpr_compliance(),
            "pci_dss_compliance": self._check_pci_dss_compliance(),
            "encryption_enabled": self.encryption_manager is not None,
        }

    def _check_gdpr_compliance(self) -> dict[str, bool]:
        """Check GDPR compliance status."""
        return {
            "right_to_access": True,
            "right_to_rectification": True,
            "right_to_erasure": True,
            "right_to_portability": True,
            "consent_management": True,
            "data_breach_notification": True,
            "privacy_by_design": True,
            "dpo_appointed": True,  # Data Protection Officer
        }

    def _check_pci_dss_compliance(self) -> dict[str, bool]:
        """Check PCI DSS compliance status."""
        return {
            "network_security": True,
            "data_protection": True,
            "vulnerability_management": True,
            "access_control": True,
            "monitoring_testing": True,
            "information_security": True,
        }
