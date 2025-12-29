"""TLS certificate generation for virtual printer services.

Generates certificates that mimic real Bambu printer certificate format:
- CA certificate mimics "BBL CA" from "BBL Technologies Co., Ltd"
- Printer certificate has CN = serial number, signed by the CA
"""

import logging
import socket
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

logger = logging.getLogger(__name__)

# Default serial number for virtual printer (matches SSDP/MQTT config)
DEFAULT_SERIAL = "00M09A391800001"


def _get_local_ip() -> str:
    """Get the local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class CertificateService:
    """Generate and manage TLS certificates for virtual printer.

    Creates a certificate chain mimicking real Bambu printers:
    - Root CA with CN="BBL CA", O="BBL Technologies Co., Ltd", C="CN"
    - Printer cert with CN=serial_number, signed by the CA
    """

    def __init__(self, cert_dir: Path, serial: str = DEFAULT_SERIAL):
        """Initialize the certificate service.

        Args:
            cert_dir: Directory to store certificates
            serial: Serial number to use as CN in printer certificate
        """
        self.cert_dir = cert_dir
        self.serial = serial
        self.ca_cert_path = cert_dir / "bbl_ca.crt"
        self.ca_key_path = cert_dir / "bbl_ca.key"
        self.cert_path = cert_dir / "virtual_printer.crt"
        self.key_path = cert_dir / "virtual_printer.key"

    def ensure_certificates(self) -> tuple[Path, Path]:
        """Ensure certificates exist, generate if needed.

        Returns:
            Tuple of (cert_path, key_path)
        """
        if self.cert_path.exists() and self.key_path.exists():
            logger.debug("Using existing virtual printer certificates")
            return self.cert_path, self.key_path
        return self.generate_certificates()

    def _generate_ca_certificate(self) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
        """Generate a CA certificate for the virtual printer.

        We use a generic name instead of mimicking BBL CA, since the slicer
        may specifically reject certificates claiming to be from BBL but
        with a different public key.

        Returns:
            Tuple of (ca_private_key, ca_certificate)
        """
        logger.info("Generating Virtual Printer CA certificate...")

        # Generate CA private key
        ca_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

        # Use a generic CA name - NOT BBL to avoid being rejected as fake
        ca_name = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, "Virtual Printer CA"),
            ]
        )

        now = datetime.now(UTC)

        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(ca_name)
            .issuer_name(ca_name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=7300))  # 20 years
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=0),
                critical=True,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(ca_key, hashes.SHA256())
        )

        return ca_key, ca_cert

    def generate_certificates(self) -> tuple[Path, Path]:
        """Generate CA and printer certificates.

        Creates a certificate chain mimicking real Bambu printers:
        - BBL CA (self-signed root)
        - Printer certificate (CN=serial, signed by BBL CA)

        Returns:
            Tuple of (cert_path, key_path)
        """
        logger.info(f"Generating certificates for virtual printer (serial: {self.serial})...")

        # Ensure directory exists
        self.cert_dir.mkdir(parents=True, exist_ok=True)

        # Generate or load CA
        ca_key, ca_cert = self._generate_ca_certificate()

        # Save CA certificate and key
        self.ca_key_path.write_bytes(
            ca_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        self.ca_key_path.chmod(0o600)
        self.ca_cert_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

        # Generate printer private key
        printer_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

        # Printer certificate subject - CN is the serial number (like real Bambu printers)
        printer_subject = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, self.serial),
            ]
        )

        # Issuer is the CA
        issuer = ca_cert.subject

        now = datetime.now(UTC)
        local_ip = _get_local_ip()
        logger.info(f"Generating printer certificate with CN={self.serial}, local IP: {local_ip}")

        # Build printer certificate signed by CA
        printer_cert = (
            x509.CertificateBuilder()
            .subject_name(printer_subject)
            .issuer_name(issuer)
            .public_key(printer_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=3650))  # 10 years
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None),
                critical=True,
            )
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.DNSName("bambuddy"),
                        x509.DNSName(self.serial),
                        x509.IPAddress(IPv4Address(local_ip)),
                        x509.IPAddress(IPv4Address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage(
                    [
                        ExtendedKeyUsageOID.SERVER_AUTH,
                        ExtendedKeyUsageOID.CLIENT_AUTH,
                    ]
                ),
                critical=False,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=True,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(ca_key, hashes.SHA256())  # Signed by CA, not self-signed
        )

        # Write printer private key
        self.key_path.write_bytes(
            printer_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        self.key_path.chmod(0o600)

        # Write printer certificate (include CA cert in chain for full chain)
        cert_chain = printer_cert.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(
            serialization.Encoding.PEM
        )
        self.cert_path.write_bytes(cert_chain)

        logger.info(f"Generated certificate chain at {self.cert_dir}")
        logger.info("  CA: CN=Virtual Printer CA")
        logger.info(f"  Printer: CN={self.serial}")
        return self.cert_path, self.key_path

    def delete_certificates(self) -> None:
        """Delete existing certificates."""
        for path in [self.cert_path, self.key_path, self.ca_cert_path, self.ca_key_path]:
            if path.exists():
                path.unlink()
        logger.info("Deleted virtual printer certificates")
