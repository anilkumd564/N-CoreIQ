"""Extract cert info and AIA from SAP Gateway leaf certificate."""
import ssl
import socket
import pathlib

HOST = "HYDRSAPT02.AMERICAS.GLOBAL.NTTDATA.COM"
PORT = 44300
OUT = pathlib.Path("cert_info_result.txt")

lines = []

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

try:
    with socket.create_connection((HOST, PORT), timeout=10) as sock:
        with ctx.wrap_socket(sock, server_hostname=HOST) as ssock:
            der = ssock.getpeercert(binary_form=True)
            lines.append(f"DER cert size: {len(der)}")
except Exception as e:
    lines.append(f"Connection failed: {e}")
    OUT.write_text("\n".join(lines))
    print("\n".join(lines))
    raise SystemExit(1)

try:
    from cryptography import x509
    cert = x509.load_der_x509_certificate(der)
    lines.append(f"Subject: {cert.subject.rfc4514_string()}")
    lines.append(f"Issuer:  {cert.issuer.rfc4514_string()}")
    try:
        lines.append(f"Not After: {cert.not_valid_after_utc}")
    except AttributeError:
        lines.append(f"Not After: {cert.not_valid_after}")

    # BasicConstraints
    try:
        bc = cert.extensions.get_extension_for_oid(
            x509.oid.ExtensionOID.BASIC_CONSTRAINTS
        )
        lines.append(f"BasicConstraints CA: {bc.value.ca}")
    except x509.ExtensionNotFound:
        lines.append("BasicConstraints: NOT PRESENT")

    # AIA
    try:
        aia = cert.extensions.get_extension_for_oid(
            x509.oid.ExtensionOID.AUTHORITY_INFORMATION_ACCESS
        )
        for desc in aia.value:
            lines.append(f"AIA: {desc.access_method._name} -> {desc.access_location.value}")
    except x509.ExtensionNotFound:
        lines.append("AIA extension: NOT PRESENT")

    # SANs
    try:
        san = cert.extensions.get_extension_for_oid(
            x509.oid.ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        )
        dns = san.value.get_values_for_type(x509.DNSName)
        lines.append(f"SANs (DNS): {dns}")
    except x509.ExtensionNotFound:
        lines.append("SAN extension: NOT PRESENT")

except ImportError:
    lines.append("cryptography library not installed - using fallback")
    # Fallback: use ssl module directly with CERT_OPTIONAL trick
    import base64
    b64 = base64.b64encode(der).decode()
    pem_lines = ["-----BEGIN CERTIFICATE-----"]
    for i in range(0, len(b64), 64):
        pem_lines.append(b64[i:i + 64])
    pem_lines.append("-----END CERTIFICATE-----")
    lines.append("Leaf cert (PEM):")
    lines.append("\n".join(pem_lines))

result = "\n".join(lines)
print(result)
OUT.write_text(result)
print(f"\nWritten to {OUT}")
