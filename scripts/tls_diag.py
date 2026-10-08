"""TLS diagnostics for SAP Gateway certificate chain."""
import ssl
import socket
import base64
import pathlib

HOST = "HYDRSAPT02.AMERICAS.GLOBAL.NTTDATA.COM"
PORT = 44300
CA_FILE = "sap_ca.pem"
OUT_FILE = "tls_diag_result.txt"

lines = []

# ── 1. Check sap_ca.pem exists ─────────────────────────────────────────────
if pathlib.Path(CA_FILE).exists():
    size = pathlib.Path(CA_FILE).stat().st_size
    lines.append(f"sap_ca.pem found: {size} bytes")
    content = pathlib.Path(CA_FILE).read_text()
    cert_count = content.count("-----BEGIN CERTIFICATE-----")
    lines.append(f"sap_ca.pem cert count: {cert_count}")
else:
    lines.append("sap_ca.pem NOT FOUND")

# ── 2. Fetch raw leaf cert (no verification) ───────────────────────────────
lines.append("\n--- Connecting with verify=NONE to inspect chain ---")
try:
    ctx_none = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx_none.check_hostname = False
    ctx_none.verify_mode = ssl.CERT_NONE
    with socket.create_connection((HOST, PORT), timeout=10) as sock:
        with ctx_none.wrap_socket(sock, server_hostname=HOST) as ssock:
            der = ssock.getpeercert(binary_form=True)
            b64 = base64.b64encode(der).decode()
            # Write leaf cert PEM
            leaf_lines = ["-----BEGIN CERTIFICATE-----"]
            for i in range(0, len(b64), 64):
                leaf_lines.append(b64[i:i+64])
            leaf_lines.append("-----END CERTIFICATE-----")
            leaf_pem = "\n".join(leaf_lines)
            lines.append(f"Leaf cert DER size: {len(der)} bytes")

            # Get cert info
            info = ssock.getpeercert()
            lines.append(f"Subject: {info.get('subject')}")
            lines.append(f"Issuer: {info.get('issuer')}")
            lines.append(f"notAfter: {info.get('notAfter')}")
            lines.append(f"SANs: {info.get('subjectAltName')}")

            # Write new sap_ca.pem with just the leaf cert
            pathlib.Path("sap_leaf.pem").write_text(leaf_pem + "\n")
            lines.append("sap_leaf.pem written (leaf only)")
except Exception as e:
    lines.append(f"CONNECT ERROR: {type(e).__name__}: {e}")

# ── 3. Test verification with sap_ca.pem ──────────────────────────────────
lines.append("\n--- Testing verify with sap_ca.pem ---")
if pathlib.Path(CA_FILE).exists():
    try:
        ctx_v = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx_v.check_hostname = False
        ctx_v.verify_mode = ssl.CERT_REQUIRED
        ctx_v.load_verify_locations(CA_FILE)
        with socket.create_connection((HOST, PORT), timeout=10) as sock:
            with ctx_v.wrap_socket(sock, server_hostname=HOST) as ssock:
                lines.append("VERIFY WITH CA_FILE: PASS")
    except Exception as e:
        lines.append(f"VERIFY WITH CA_FILE: FAIL - {type(e).__name__}: {e}")

# ── 4. Test verification with leaf cert ───────────────────────────────────
lines.append("\n--- Testing verify with leaf cert (sap_leaf.pem) ---")
if pathlib.Path("sap_leaf.pem").exists():
    try:
        ctx_v2 = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx_v2.check_hostname = False
        ctx_v2.verify_mode = ssl.CERT_REQUIRED
        ctx_v2.load_verify_locations("sap_leaf.pem")
        with socket.create_connection((HOST, PORT), timeout=10) as sock:
            with ctx_v2.wrap_socket(sock, server_hostname=HOST) as ssock:
                lines.append("VERIFY WITH LEAF: PASS")
    except Exception as e:
        lines.append(f"VERIFY WITH LEAF: FAIL - {type(e).__name__}: {e}")

# ── 5. Test system trust store ─────────────────────────────────────────────
lines.append("\n--- Testing verify with system trust store ---")
try:
    ctx_sys = ssl.create_default_context()
    ctx_sys.check_hostname = False
    with socket.create_connection((HOST, PORT), timeout=10) as sock:
        with ctx_sys.wrap_socket(sock, server_hostname=HOST) as ssock:
            lines.append("VERIFY WITH SYSTEM STORE: PASS")
except Exception as e:
    lines.append(f"VERIFY WITH SYSTEM STORE: FAIL - {type(e).__name__}: {e}")

# ── Output ─────────────────────────────────────────────────────────────────
result = "\n".join(lines)
print(result)
pathlib.Path(OUT_FILE).write_text(result)
print(f"\nResults written to {OUT_FILE}")
