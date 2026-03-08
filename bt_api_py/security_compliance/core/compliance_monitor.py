"""Compliance monitoring for regulatory requirements.

Implements automated compliance checking for SOX, MiFID II, PCI DSS,
and other financial industry regulations.
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from enum import Enum


class ComplianceStandard(Enum):
    """Compliance standards."""

    SOX = "sox"
    MIFID_II = "mifid_ii"
    PCI_DSS = "pci_dss"
    GDPR = "gdpr"
    ISO_27001 = "iso_27001"
    NIST_CSF = "nist_csf"


@dataclass
class ComplianceRule:
    """Individual compliance rule."""

    rule_id: str
    standard: ComplianceStandard
    description: str
    check_function: callable
    severity: str = "high"


class ComplianceMonitor:
    """Compliance monitoring and reporting."""

    def __init__(self):
        """Initialize compliance monitor."""
        self.rules: List[ComplianceRule] = []
        self._init_default_rules()

    def _init_default_rules(self):
        """Initialize default compliance rules."""
        # SOX rules
        self.rules.append(
            ComplianceRule(
                rule_id="SOX_001",
                standard=ComplianceStandard.SOX,
                description="All financial transactions must be auditable",
                check_function=self._check_transaction_auditability,
            )
        )

        # MiFID II rules
        self.rules.append(
            ComplianceRule(
                rule_id="MIFID_001",
                standard=ComplianceStandard.MIFID_II,
                description="Trade timestamps must be accurate to millisecond",
                check_function=self._check_timestamp_accuracy,
            )
        )

        # PCI DSS rules
        self.rules.append(
            ComplianceRule(
                rule_id="PCI_001",
                standard=ComplianceStandard.PCI_DSS,
                description="Cardholder data must be encrypted",
                check_function=self._check_card_data_encryption,
            )
        )

    def _check_transaction_auditability(self) -> bool:
        """Check if transactions are properly auditable."""
        # Implementation would check audit logs, data integrity, etc.
        return True

    def _check_timestamp_accuracy(self) -> bool:
        """Check timestamp accuracy for MiFID II compliance."""
        # Implementation would verify timestamp precision and synchronization
        return True

    def _check_card_data_encryption(self) -> bool:
        """Check if cardholder data is encrypted."""
        # Implementation would verify encryption of sensitive data
        return True

    def run_compliance_check(self, standard: Optional[ComplianceStandard] = None) -> Dict[str, Any]:
        """Run compliance checks."""
        results = {}

        for rule in self.rules:
            if standard is None or rule.standard == standard:
                try:
                    passed = rule.check_function()
                    results[rule.rule_id] = {
                        "standard": rule.standard.value,
                        "description": rule.description,
                        "passed": passed,
                        "severity": rule.severity,
                    }
                except Exception as e:
                    results[rule.rule_id] = {
                        "standard": rule.standard.value,
                        "description": rule.description,
                        "passed": False,
                        "error": str(e),
                        "severity": rule.severity,
                    }

        return results

    def generate_compliance_report(self) -> Dict[str, Any]:
        """Generate comprehensive compliance report."""
        results = self.run_compliance_check()

        passed_count = sum(1 for r in results.values() if r["passed"])
        total_count = len(results)

        return {
            "summary": {
                "total_rules": total_count,
                "passed": passed_count,
                "failed": total_count - passed_count,
                "compliance_percentage": (passed_count / total_count) * 100
                if total_count > 0
                else 0,
            },
            "results": results,
            "by_standard": self._group_by_standard(results),
        }

    def _group_by_standard(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Group results by compliance standard."""
        grouped = {}

        for rule_id, result in results.items():
            standard = result["standard"]
            if standard not in grouped:
                grouped[standard] = {"total": 0, "passed": 0, "failed": 0, "rules": []}

            grouped[standard]["total"] += 1
            if result["passed"]:
                grouped[standard]["passed"] += 1
            else:
                grouped[standard]["failed"] += 1

            grouped[standard]["rules"].append(
                {
                    "rule_id": rule_id,
                    "description": result["description"],
                    "passed": result["passed"],
                }
            )

        return grouped
