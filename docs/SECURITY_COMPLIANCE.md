# Security Compliance and Audit Framework

bt_api_py includes a comprehensive security compliance and audit framework designed to meet the stringent requirements of financial industry regulations including PCI DSS Level 1, SOX 404, MiFID II, GDPR, ISO 27001, NIST Cybersecurity Framework, and SOC 2 Type II compliance.

## 🏛️ Architecture Overview

The security framework implements a zero-trust architecture with the following core components:

### 🔐 Core Security Components

- **Access Control**: RBAC + ABAC with fine-grained permissions
- **Audit Logging**: Tamper-evident, cryptographically secure audit trails
- **Encryption**: FIPS 140-2 Level 3 compliant end-to-end encryption
- **Authentication**: OAuth 2.0 + OpenID Connect with WebAuthn/FIDO2
- **Multi-Factor Authentication**: TOTP, HOTP, and hardware security keys
- **Data Protection**: GDPR-compliant data lifecycle management
- **Threat Detection**: Real-time security monitoring and anomaly detection
- **Disaster Recovery**: Business continuity and automated backup systems

## 🚀 Quick Start

### 1. Installation

```bash
# Install with security dependencies
pip install bt_api_py[security]

# Optional: Install additional security packages
pip install cryptography pyotp boto3 hvac qrcode[pil] cbor2
```

### 2. Configuration

```bash
# Copy configuration template
cp configs/examples/security_compliance.env.example .env

# Edit configuration with your security settings
nano .env
```

### 3. Basic Usage

```python
from bt_api_py.security_compliance import initialize_security_framework

# Initialize security framework
framework = initialize_security_framework()

# Create user with access control
user = framework.access_control.create_user(
    user_id="trader_001",
    username="john_trader",
    email="john@company.com"
)

# Assign role
framework.access_control.assign_role("trader_001", "trader")

# Check permissions
has_access = framework.access_control.check_permission(
    "trader_001", 
    Resource.ORDER_MANAGEMENT, 
    "create", 
    PermissionLevel.WRITE
)
```

## 📋 Compliance Standards

### PCI DSS Level 1
- ✅ Encryption of cardholder data
- ✅ Strong access control measures
- ✅ Regular monitoring and testing
- ✅ Secure network architecture
- ✅ Vulnerability management program

### SOX 404
- ✅ Comprehensive audit trails
- ✅ 7-year log retention
- ✅ Internal controls documentation
- ✅ Management assessment

### MiFID II
- ✅ Trade timestamp accuracy (millisecond)
- ✅ Complete audit trail
- ✅ Record-keeping requirements
- ✅ Best execution reporting

### GDPR
- ✅ Right to be forgotten
- ✅ Data portability
- ✅ Consent management
- ✅ Data breach notification

### ISO 27001
- ✅ Information security management
- ✅ Risk assessment framework
- ✅ Continuous improvement
- ✅ Third-party audits

## 🔧 Configuration

### Environment Variables

```bash
# Encryption
SECURITY_KEY_PROVIDER=local  # local, aws_kms, hashicorp_vault
ENCRYPTION_ALGORITHM=aes_256_gcm
AUTO_KEY_ROTATION=false

# Audit Logging
AUDIT_LOG_FILE=./logs/audit.log
AUDIT_RETENTION_DAYS=2555  # 7 years for SOX
AUDIT_REAL_TIME=true

# Authentication
OAUTH2_ISSUER_URL=https://localhost:8443
MFA_ISSUER_NAME=bt_api_py
```

### Security Configuration File

```python
security_config = {
    "encryption": {
        "provider": "local",
        "algorithm": "aes_256_gcm",
        "auto_rotate": False
    },
    "audit": {
        "log_file": "./logs/audit.log",
        "retention_days": 2555,
        "real_time": True
    },
    "compliance": {
        "pci_dss_level": 1,
        "sox_compliance": True,
        "mifid_ii_compliance": True,
        "gdpr_compliance": True
    }
}
```

## 🛡️ Security Features

### Zero Trust Architecture

Every request is authenticated and authorized with minimal privilege principle:

```python
# Decorator for securing functions
@require_permission(Resource.ORDER_MANAGEMENT, "create", PermissionLevel.WRITE)
@audit_access(EventType.ORDER_CREATED, SeverityLevel.HIGH)
def create_order(user_id, symbol, quantity, price):
    # Function implementation
    pass
```

### End-to-End Encryption

```python
# Encrypt sensitive data
encrypted_data = framework.encryption_manager.encrypt({
    "card_number": "4111111111111111",
    "amount": 1000.00
})

# Decrypt when needed
decrypted_data = framework.encryption_manager.decrypt(encrypted_data)
```

### Audit Logging

```python
# Log security events
framework.audit_logger.log_event(
    EventType.USER_LOGIN,
    user_id="user_123",
    severity=SeverityLevel.MEDIUM,
    ip_address="192.168.1.100",
    details={"login_method": "mfa"}
)
```

### Multi-Factor Authentication

```python
# Setup TOTP for user
totp_setup = framework.mfa_provider.setup_totp("user_123")
print(f"QR Code: {totp_setup['qr_code']}")

# Verify TOTP token
is_valid = framework.mfa_provider.verify_totp("user_123", "123456")
```

### Data Protection

```python
# Mask sensitive data
masked_data = framework.data_protection.mask_data(
    {"email": "user@example.com", "ssn": "123-45-6789"},
    mask_level="partial"
)

# GDPR right to be forgotten
request_id = framework.data_protection.request_right_to_be_forgotten(
    "user_123", 
    "User withdrawal of consent"
)
```

## 🔍 Monitoring and Alerting

### Real-time Security Monitoring

```python
# Detect failed login attempts
threat = framework.security_monitoring.threat_detector.detect_failed_login(
    "user_123", "192.168.1.100"
)

if threat:
    # Auto-respond to threat
    response = framework.security_monitoring.threat_detector.auto_respond_to_threat(threat)
```

### Compliance Monitoring

```python
# Run compliance checks
compliance_report = framework.security_monitoring.compliance_monitor.run_compliance_check()

# Generate specific compliance reports
sox_report = framework.security_monitoring.compliance_monitor.generate_compliance_report(
    compliance_standard="SOX"
)
```

## 🔄 Integration with bt_api_py

### Secure API Integration

```python
from bt_api_py import BtApi
from bt_api_py.security_compliance import integrate_with_bt_api

# Initialize bt_api_py with security
bt_api = BtApi()
secure_bt_api = integrate_with_bt_api(bt_api)

# All API calls now require proper authentication and authorization
try:
    # This will fail without proper permissions
    feed = secure_bt_api.create_feed(
        "BINANCE___SPOT",
        user_id="trader_001"
    )
except AccessDeniedError as e:
    print(f"Access denied: {e}")
```

### Decorators for Existing Functions

```python
from bt_api_py.security_compliance import require_permission, audit_access

# Apply security to existing functions
@require_permission(Resource.MARKET_DATA, "read", PermissionLevel.READ)
@audit_access(EventType.DATA_ACCESSED, SeverityLevel.LOW)
def get_market_data(symbol, user_id=None):
    # Existing implementation
    pass
```

## 📊 Compliance Reporting

### Generate Compliance Reports

```python
# SOX Compliance Report
sox_report = framework.audit_logger.get_compliance_report(
    "SOX",
    start_date="2024-01-01",
    end_date="2024-03-31"
)

# PCI DSS Compliance Report
pci_report = framework.audit_logger.get_compliance_report(
    "PCI_DSS"
)

# GDPR Data Protection Report
gdpr_report = framework.data_protection.generate_data_protection_report()
```

### Audit Trail Verification

```python
# Verify audit log integrity
integrity_report = framework.audit_logger.verify_log_integrity()

print(f"Audit log integrity: {integrity_report['status']}")
print(f"Verified events: {integrity_report['verified_events']}")
print(f"Violations: {len(integrity_report['violations'])}")
```

## 🚨 Threat Detection and Response

### Automated Threat Detection

```python
# Configure threat detection thresholds
threat_detector = framework.security_monitoring.threat_detector
threat_detector.config["thresholds"] = {
    "failed_login_threshold": 5,
    "suspicious_ip_threshold": 10,
    "data_access_threshold": 1000
}

# Monitor for threats
threats = threat_detector.get_threat_summary(time_window=3600)  # Last hour
```

### Incident Response

```python
# Create security alert
alert = framework.security_monitoring.create_alert(
    title="Suspicious Login Activity",
    description="Multiple failed login attempts detected",
    severity=AlertSeverity.HIGH,
    source="authentication_service",
    user_id="user_123",
    ip_address="192.168.1.100"
)

# Acknowledge and resolve
framework.security_monitoring.acknowledge_alert(alert.alert_id)
framework.security_monitoring.resolve_alert(alert.alert_id)
```

## 🔄 Disaster Recovery

### Backup Configuration

```python
# Create backup configuration
backup_config = framework.disaster_recovery.create_backup_config(
    name="Daily Database Backup",
    frequency="daily",
    retention_days=30,
    locations=["s3://backup-bucket/", "/local/backups/"]
)

# Initiate backup
backup_result = framework.disaster_recovery.initiate_backup(backup_config.backup_id)
```

### Recovery Planning

```python
# Create recovery plan
recovery_plan = framework.disaster_recovery.create_recovery_plan(
    name="Primary System Recovery",
    description="Recovery procedures for system outages",
    disaster_types=["server_failure", "data_corruption"],
    recovery_steps=[
        "Assess disaster scope",
        "Activate recovery team", 
        "Restore from backup",
        "Verify system integrity"
    ],
    rto_hours=4,
    rpo_hours=24
)

# Test recovery plan
test_result = framework.disaster_recovery.test_recovery_plan(recovery_plan.plan_id)
```

## 📋 Compliance Checklist

### PCI DSS Level 1 Checklist

- [ ] Install and maintain firewall configuration
- [ ] Do not use vendor-supplied defaults for system passwords
- [ ] Protect stored cardholder data
- [ ] Encrypt transmission of cardholder data
- [ ] Use and regularly update anti-virus software
- [ ] Develop and maintain secure systems and applications
- [ ] Restrict access to cardholder data by business need-to-know
- [ ] Assign a unique ID to each person with computer access
- [ ] Restrict physical access to cardholder data
- [ ] Track and monitor all access to network resources
- [ ] Regularly test security systems and processes
- [ ] Maintain an information security policy

### SOX 404 Checklist

- [ ] Document all financial processes
- [ ] Establish internal controls
- [ ] Conduct regular risk assessments
- [ ] Implement monitoring systems
- [ ] Maintain audit trails for 7 years
- [ ] Perform management assessments
- [ ] Conduct external audits
- [ ] Document control deficiencies
- [ ] Implement remediation plans

### GDPR Checklist

- [ ] Obtain lawful basis for processing
- [ ] Implement privacy by design
- [ ] Maintain records of processing activities
- [ ] Provide privacy notices
- [ ] Handle data subject requests
- [ ] Report data breaches within 72 hours
- [ ] Conduct Data Protection Impact Assessments (DPIA)
- [ ] Appoint Data Protection Officer (DPO)

## 🔧 Best Practices

### 1. Security Configuration

```python
# Use strong encryption
framework.encryption_manager.rotate_keys()  # Rotate keys regularly

# Enable MFA for all users
users = framework.access_control._users.values()
for user in users:
    if not framework.mfa_provider.is_mfa_enabled(user.user_id):
        # Enable MFA for user
        pass
```

### 2. Audit Trail Management

```python
# Regular integrity checks
import schedule

def verify_audit_integrity():
    integrity = framework.audit_logger.verify_log_integrity()
    if integrity['violations']:
        # Send alert for audit integrity issues
        pass

# Schedule daily integrity checks
schedule.every().day.at("02:00").do(verify_audit_integrity)
```

### 3. Compliance Monitoring

```python
# Regular compliance checks
def daily_compliance_check():
    sox_compliance = framework.security_monitoring.compliance_monitor.run_compliance_check(
        ComplianceStandard.SOX
    )
    
    if sox_compliance['summary']['compliance_percentage'] < 100:
        # Alert compliance team
        pass

# Schedule daily compliance checks
schedule.every().day.at("08:00").do(daily_compliance_check)
```

## 🚀 Production Deployment

### Docker Security

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . /app
RUN pip install ".[security]"

# Security best practices
RUN adduser --disabled-password --gecos '' appuser
USER appuser

# Run with security framework
CMD ["python", "-m", "bt_api_py.security_compliance.server"]
```

### Kubernetes Security

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: security-config
data:
  SECURITY_KEY_PROVIDER: "aws_kms"
  AUDIT_LOG_FILE: "/var/log/audit.log"
  AUDIT_RETENTION_DAYS: "2555"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bt-api-secure
spec:
  template:
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
      containers:
      - name: bt-api
        image: bt-api:latest
        envFrom:
        - configMapRef:
            name: security-config
        volumeMounts:
        - name: audit-log
          mountPath: /var/log
      volumes:
      - name: audit-log
        persistentVolumeClaim:
          claimName: audit-log-pvc
```

## 📚 Additional Resources

### Documentation
- [API Reference](https://cloudquant.github.io/bt_api_py/security/)
- [Configuration Guide](https://cloudquant.github.io/bt_api_py/security/config/)
- [Compliance Guide](https://cloudquant.github.io/bt_api_py/security/compliance/)

### Support
- Security Issues: security@bt_api_py.com
- Compliance Questions: compliance@bt_api_py.com
- Documentation: https://docs.bt_api_py.com

---

**Security is everyone's responsibility.** This framework provides the tools and controls needed to maintain regulatory compliance while enabling secure trading operations. Regular security assessments, compliance audits, and threat monitoring are essential for maintaining the security posture of your trading systems.
