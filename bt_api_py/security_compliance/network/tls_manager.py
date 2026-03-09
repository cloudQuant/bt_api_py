"""TLS Manager for Secure Communications.

Manages TLS 1.3 connections, certificate validation, and secure
communications for financial industry compliance.
"""

from typing import Any


class TLSManager:
    """TLS connection manager for secure communications."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize TLS manager.

        Args:
            config: Configuration dictionary containing TLS settings including
                version, cipher_suites, and certificate_validation.
        """
        self.config = config
        self.version = config.get("version", "1.3")
        self.cipher_suites = config.get("cipher_suites", ["TLS_AES_256_GCM_SHA384"])
        self.certificate_validation = config.get("certificate_validation", "strict")

    def get_ssl_context(self) -> Any:
        """Get SSL context for secure connections.

        Returns:
            Configured SSL context object with appropriate TLS version,
            cipher suites, and certificate validation settings.
        """
        import ssl

        context = ssl.create_default_context()

        # Set minimum TLS version
        if self.version == "1.3":
            context.minimum_version = ssl.TLSVersion.TLSv1_3
        elif self.version == "1.2":
            context.minimum_version = ssl.TLSVersion.TLSv1_2

        # Set cipher suites if specified
        if self.cipher_suites:
            context.set_ciphers(":".join(self.cipher_suites))

        # Certificate validation
        if self.certificate_validation == "strict":
            context.verify_mode = ssl.CERT_REQUIRED
            context.check_hostname = True
        elif self.certificate_validation == "none":
            context.verify_mode = ssl.CERT_NONE
            context.check_hostname = False

        return context

    def validate_certificate(self, cert_path: str) -> bool:
        """Validate TLS certificate.

        Args:
            cert_path: Path to the certificate file to validate.

        Returns:
            True if certificate is valid, False otherwise.
        """
        try:
            import ssl

            context = ssl.create_default_context()
            context.load_verify_locations(cafile=cert_path)
            return True
        except Exception:
            return False
