"""A tiny certificate authority for scheduler <-> worker mutual TLS.

`helper certs init` creates a CA; `helper certs issue` signs a certificate
for a worker (server) or a scheduler (client). The role is recorded in the
certificate's OU so a worker only accepts schedulers, not other workers.
"""

import datetime
import ipaddress
import logging
import os
import ssl

from pathlib import Path

from rpyc.utils.authenticators import AuthenticationError

from .log import warn

WORKER = "worker"
SCHEDULER = "scheduler"
ROLES = (WORKER, SCHEDULER)

_logger = logging.getLogger("worker.auth")

CA_NAME = "ca"


def pki_dir() -> Path:
    env = os.environ.get("EXPERIMENT_PKI_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "experiment" / "pki"


def paths(name: str, directory: Path | None = None) -> tuple[Path, Path]:
    """(certificate, key) paths for `name` in the pki directory."""
    directory = directory or pki_dir()
    return directory / f"{name}.crt", directory / f"{name}.key"


def ca_path(directory: Path | None = None) -> Path:
    return paths(CA_NAME, directory)[0]


def check_key_permissions(key: Path) -> None:
    mode = key.stat().st_mode
    if mode & 0o077:
        warn(
            f"{key} is readable by other users (mode {oct(mode & 0o777)}). "
            f"Run: chmod 600 {key}"
        )


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def _new_key():
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec.generate_private_key(ec.SECP256R1())


def _key_bytes(key) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _cert_bytes(cert) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return cert.public_bytes(serialization.Encoding.PEM)


def init_ca(directory: Path | None = None, days: int = 3650) -> Path:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import NameOID

    directory = directory or pki_dir()
    cert_path, key_path = paths(CA_NAME, directory)
    if cert_path.exists() or key_path.exists():
        raise FileExistsError(
            f"A CA already exists in {directory}; refusing to overwrite it."
        )
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)

    key = _new_key()
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "experiment CA")]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0), critical=True
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
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
        .sign(key, hashes.SHA256())
    )
    _write_private(key_path, _key_bytes(key))
    cert_path.write_bytes(_cert_bytes(cert))
    return cert_path


def issue(
    role: str,
    name: str,
    sans: list[str] = (),
    directory: Path | None = None,
    days: int = 825,
) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    directory = directory or pki_dir()
    ca_cert_path, ca_key_path = paths(CA_NAME, directory)
    if not ca_key_path.exists():
        raise FileNotFoundError(
            f"No CA in {directory}. Run `helper certs init` first."
        )
    cert_path, key_path = paths(name, directory)
    if cert_path.exists() or key_path.exists():
        raise FileExistsError(
            f"{cert_path} already exists; delete it first to reissue."
        )
    ca_key = serialization.load_pem_private_key(
        ca_key_path.read_bytes(), password=None
    )
    ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())

    alt_names = []
    for san in [name, *sans] if role == WORKER else sans:
        try:
            alt_names.append(x509.IPAddress(ipaddress.ip_address(san)))
        except ValueError:
            alt_names.append(x509.DNSName(san))

    key = _new_key()
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, name),
                    x509.NameAttribute(
                        NameOID.ORGANIZATIONAL_UNIT_NAME, role
                    ),
                ]
            )
        )
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [
                    (
                        ExtendedKeyUsageOID.SERVER_AUTH
                        if role == WORKER
                        else ExtendedKeyUsageOID.CLIENT_AUTH
                    )
                ]
            ),
            critical=False,
        )
    )
    if alt_names:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(alt_names), critical=False
        )
    cert = builder.sign(ca_key, hashes.SHA256())
    _write_private(key_path, _key_bytes(key))
    cert_path.write_bytes(_cert_bytes(cert))
    return cert_path, key_path


def peer_role(peercert: dict | None) -> str | None:
    for rdn in (peercert or {}).get("subject", ()):
        for key, value in rdn:
            if key == "organizationalUnitName":
                return value
    return None


class RoleAuthenticator:
    """rpyc authenticator: mutual TLS, and the peer must have `role`."""

    def __init__(self, cert: Path, key: Path, ca: Path, role: str) -> None:
        check_key_permissions(key)
        self._role = role
        self._context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self._context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._context.load_cert_chain(cert, keyfile=key)
        self._context.load_verify_locations(ca)
        self._context.verify_mode = ssl.CERT_REQUIRED

    def __call__(self, sock):
        try:
            peer = sock.getpeername()
        except OSError:
            peer = "?"
        try:
            sock2 = self._context.wrap_socket(sock, server_side=True)
        except (ssl.SSLError, OSError) as e:
            _logger.warning(f"TLS handshake with {peer} failed: {e}")
            raise AuthenticationError(str(e))
        peercert = sock2.getpeercert()
        role = peer_role(peercert)
        if role != self._role:
            sock2.close()
            _logger.warning(
                f"Rejected {peer}: certificate role is {role!r}, "
                f"not {self._role!r}"
            )
            raise AuthenticationError(
                f"peer certificate role is not {self._role!r}"
            )
        return sock2, peercert
