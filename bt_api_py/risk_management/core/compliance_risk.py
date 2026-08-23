"""合规风险计算（合规评分/监管违规/报告合规/审计/政策遵循）。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from ..containers.risk_metrics import ComplianceRiskMetrics


class ComplianceRiskMixin:
    """合规风险计算方法（供 RiskCalculator 混入）。"""

    def _calculate_compliance_risk(self, account_data: dict[str, Any]) -> ComplianceRiskMetrics:
        """"""

        #
        compliance_score = self._calculate_compliance_score(account_data)

        #
        regulatory_violations = self._get_regulatory_violations(account_data)

        #
        reporting_compliance = self._calculate_reporting_compliance(account_data)

        #
        audit_findings = self._get_audit_findings(account_data)

        #
        policy_adherence = self._calculate_policy_adherence(account_data)

        # KYC
        kyc_status = account_data.get("kyc_status", "UNKNOWN")

        # AML
        aml_flags = self._get_aml_flags(account_data)

        return ComplianceRiskMetrics(
            {
                "compliance_score": compliance_score,
                "regulatory_violations": regulatory_violations,
                "reporting_compliance": reporting_compliance,
                "audit_findings": audit_findings,
                "policy_adherence": policy_adherence,
                "kyc_status": kyc_status,
                "aml_flags": aml_flags,
            }
        )

    def _calculate_compliance_score(self, account_data: dict[str, Any]) -> Decimal:
        """"""
        kyc_compliance = 1.0 if account_data.get("kyc_status") == "VERIFIED" else 0.0
        aml_compliance = 1.0 if not account_data.get("aml_flags", []) else 0.5
        reporting_compliance = account_data.get("reporting_compliance", 0.9)
        policy_compliance = account_data.get("policy_compliance", 0.85)

        compliance_score = (
            kyc_compliance + aml_compliance + reporting_compliance + policy_compliance
        ) / 4
        return Decimal(str(compliance_score))

    def _get_regulatory_violations(self, account_data: dict[str, Any]) -> list[dict[str, Any]]:
        """"""
        return cast("list[dict[str, Any]]", account_data.get("regulatory_violations", []))

    def _calculate_reporting_compliance(self, account_data: dict[str, Any]) -> Decimal:
        """"""
        required_reports = account_data.get("required_reports", 100)
        submitted_reports = account_data.get("submitted_reports", 95)

        compliance_rate = submitted_reports / required_reports if required_reports > 0 else 0
        return Decimal(str(compliance_rate))

    def _get_audit_findings(self, account_data: dict[str, Any]) -> list[dict[str, Any]]:
        """"""
        return cast("list[dict[str, Any]]", account_data.get("audit_findings", []))

    def _calculate_policy_adherence(self, account_data: dict[str, Any]) -> Decimal:
        """"""
        policy_checks = account_data.get("policy_checks", [])
        if not policy_checks:
            return Decimal("1.0")

        passed_checks = sum(1 for check in policy_checks if check.get("passed", False))
        adherence_rate = passed_checks / len(policy_checks)

        return Decimal(str(adherence_rate))

    def _get_aml_flags(self, account_data: dict[str, Any]) -> list[str]:
        """AML"""
        return cast("list[str]", account_data.get("aml_flags", []))
