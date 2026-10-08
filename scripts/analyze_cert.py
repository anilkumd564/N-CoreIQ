"""Analyze the extracted SAP certificate to determine if it can serve as a CA bundle."""
import ssl
import sys
import os

cert_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sap_ca.pem")

with open(cert_path) as f:
    pem = f.read()

print(f"PEM length: {len(pem)} chars")
print()

# Test 1: Can ssl load it as a CA cert?
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_OPTIONAL
try:
    ctx.load_verify_locations(cadata=pem)
    print("RESULT: ssl.load_verify_locations() ACCEPTED the cert as a CA cert")
    print("  -> This cert CAN be used as a CA bundle with httpx verify=path")
    is_ca_loadable = True
except Exception as e:
    print(f"RESULT: ssl.load_verify_locations() REJECTED: {e}")
    is_ca_loadable = False

print()

# Test 2: Use cryptography if available
try:
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend

    cert = x509.load_pem_x509_certificate(pem.encode(), default_backend())
    print(f"Subject : {cert.subject.rfc4514_string()}")
    print(f"Issuer  : {cert.issuer.rfc4514_string()}")
    print(f"Serial  : {cert.serial_number}")
    print(f"NotAfter: {cert.not_valid_after}")

    try:
        bc = cert.extensions.get_extension_for_oid(x509.ExtensionOID.BASIC_CONSTRAINTS)
        print(f"BasicConstraints CA: {bc.value.ca}")
        if bc.value.ca:
            print("  -> SELF-SIGNED ROOT CA or intermediate CA cert")
    except x509.ExtensionNotFound:
        print("BasicConstraints: NOT PRESENT (not a CA cert by extension)")

    try:
        ski_ext = cert.extensions.get_extension_for_oid(x509.ExtensionOID.SUBJECT_KEY_IDENTIFIER)
        ski = ski_ext.value.key_identifier.hex()
        print(f"SKI: {ski}")
    except x509.ExtensionNotFound:
        ski = None
        print("SKI: NOT PRESENT")

    try:
        aki_ext = cert.extensions.get_extension_for_oid(x509.ExtensionOID.AUTHORITY_KEY_IDENTIFIER)
        if aki_ext.value.key_identifier:
            aki = aki_ext.value.key_identifier.hex()
            print(f"AKI: {aki}")
            if ski and ski == aki:
                print("  -> SKI == AKI: SELF-SIGNED CERT")
            else:
                print("  -> SKI != AKI: cert is signed by a different CA")
        else:
            print("AKI: no key_identifier field")
    except x509.ExtensionNotFound:
        print("AKI: NOT PRESENT")

    try:
        aia = cert.extensions.get_extension_for_oid(x509.ExtensionOID.AUTHORITY_INFORMATION_ACCESS)
        for access in aia.value:
            print(f"AIA: {access.access_method} -> {access.access_location.value}")
    except x509.ExtensionNotFound:
        print("AIA: NOT PRESENT (no CA cert download URL in cert)")

except ImportError:
    print("cryptography package not installed — skipping detailed analysis")

print()
print(f"CA bundle usable with httpx verify=path: {is_ca_loadable}")
