"""Creates the TLS certificates and MQTT logins for the Mosquitto broker.
Works on Windows, macOS and Linux (only needs Python + `pip install cryptography`).

Usage:
    python scripts/setup_broker.py <broker-ip-or-hostname> [device-id ...]
    python scripts/setup_broker.py --cert-only <new-ip>     (laptop IP changed)
Example:
    python scripts/setup_broker.py 192.168.1.50 shelf-01 shelf-sim

The ESP32 must connect to exactly the IP/hostname given here, because it is
written into the broker certificate. Run again whenever that address changes
(the CA is kept, so devices keep trusting it). Without --cert-only the
passwords are regenerated too; with --cert-only they stay as they are.

Creates (all git-ignored): broker/certs/ca.crt, ca.key, server.crt, server.key
and broker/config/passwd. Prints the passwords to put in .env and config.h.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import ipaddress
import os
import secrets
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parents[1]
CERTS = ROOT / "broker" / "certs"
PASSWD = ROOT / "broker" / "config" / "passwd"


def new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def write_key(path: Path, key) -> None:
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                       serialization.NoEncryption()))


def name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def load_or_create_ca():
    key_file, crt_file = CERTS / "ca.key", CERTS / "ca.crt"
    if key_file.exists() and crt_file.exists():
        key = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
        return key, x509.load_pem_x509_certificate(crt_file.read_bytes())
    key = new_key()
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name("Shelf-Stock Monitor CA"))
        .issuer_name(name("Shelf-Stock Monitor CA"))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=825))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .sign(key, hashes.SHA256())
    )
    write_key(key_file, key)
    crt_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print("created certificate authority broker/certs/ca.crt")
    return key, cert


def create_server_cert(host: str, ca_key, ca_cert) -> None:
    sans: list[x509.GeneralName] = [x509.DNSName("mosquitto"), x509.DNSName("localhost"),
                                    x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    try:
        sans.insert(0, x509.IPAddress(ipaddress.ip_address(host)))
    except ValueError:
        sans.insert(0, x509.DNSName(host))
    key = new_key()
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name(host))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    write_key(CERTS / "server.key", key)
    (CERTS / "server.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"created broker certificate for {host} (+ mosquitto, localhost)")


def mosquitto_hash(password: str) -> str:
    """Same format as `mosquitto_passwd` (PBKDF2-SHA512, '$7$')."""
    iterations = 101
    salt = os.urandom(12)
    digest = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, iterations)
    return f"$7${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def main() -> None:
    args = sys.argv[1:]
    cert_only = "--cert-only" in args
    args = [a for a in args if a != "--cert-only"]
    if not args:
        sys.exit(__doc__)
    host = args[0]
    devices = args[1:] or ["shelf-01"]
    CERTS.mkdir(parents=True, exist_ok=True)
    ca_key, ca_cert = load_or_create_ca()
    create_server_cert(host, ca_key, ca_cert)
    for f in CERTS.glob("*"):
        f.chmod(0o644)  # Mosquitto runs as its own user inside Docker
    if cert_only:
        print(f"Passwords unchanged. In config.h set MQTT_HOST to \"{host}\", re-upload, then:")
        print("  docker compose restart mosquitto")
        return

    users = {"cloud": secrets.token_urlsafe(18)}
    users.update({d: secrets.token_urlsafe(18) for d in devices})
    PASSWD.write_text("".join(f"{u}:{mosquitto_hash(p)}\n" for u, p in users.items()))
    PASSWD.chmod(0o644)

    print("\n=== put this line in .env ===")
    print(f"MQTT_PASSWORD={users['cloud']}")
    for d in devices:
        print(f"\n=== device {d}: put these lines in firmware/shelf_node/config.h (or use with the simulator) ===")
        print(f'#define MQTT_HOST "{host}"')
        print(f'#define DEVICE_ID "{d}"')
        print(f'#define MQTT_PASSWORD "{users[d]}"')
    print("\nAlso paste the contents of broker/certs/ca.crt into CA_CERT in config.h.")
    print("Then restart the broker:  docker compose restart mosquitto")


if __name__ == "__main__":
    main()
