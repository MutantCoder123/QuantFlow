"""A self-signed certificate for the dashboard on the Wi-Fi (HTTPS, port 8443),
so the password and the data are encrypted on a shared network.

Made on this PC (data/tls/, gitignored), valid for localhost, 127.0.0.1 and the
PC's current Wi-Fi address; made again when that address changes or within 30
days of expiry. Browsers warn once per device (the certificate is not from a
public authority) -- accept it once.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import socket
from pathlib import Path

VALID_DAYS = 825


def lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))                 # picks the Wi-Fi interface; sends nothing
            return s.getsockname()[0]
    except OSError:
        return None


def _covers(cert_path: Path, ip: str | None) -> bool:
    from cryptography import x509
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (OSError, ValueError):
        return False
    if cert.not_valid_after_utc - dt.datetime.now(dt.timezone.utc) < dt.timedelta(days=30):
        return False
    if not ip:
        return True
    try:
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False
    return ipaddress.ip_address(ip) in sans.get_values_for_type(x509.IPAddress)


def ensure_cert(tls_dir: Path, ip: str | None = None) -> tuple[Path, Path]:
    """(cert, key) paths, making a new pair when needed."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    tls_dir = Path(tls_dir)
    cert_path, key_path = tls_dir / "dashboard.crt", tls_dir / "dashboard.key"
    if key_path.exists() and _covers(cert_path, ip):
        return cert_path, key_path
    tls_dir.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "QuantFlow dashboard")])
    sans = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    if ip:
        sans.append(x509.IPAddress(ipaddress.ip_address(ip)))
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=VALID_DAYS))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path
