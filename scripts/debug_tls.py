"""Debug TLS connection to SAP with detailed error reporting."""
import ssl
import socket
import base64
import os

SAP_HOST = "HYDRSAPT02.AMERICAS.GLOBAL.NTTDATA.COM"
SAP_PORT = 44300
CA_BUNDLE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sap_ca.pem")

print(f"CA bundle: {CA_BUNDLE}")
print(f"CA bundle exists: {os.path.exists(CA_BUNDLE)}")
print()

# Step 1: Try to get the raw chain via CERT_NONE
print("Step 1: Connecting with CERT_NONE to inspect chain...")
try:
    ctx_none = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx_none.check_hostname = False
    ctx_none.verify_mode = ssl.CERT_NONE

    with socket.create_connection((SAP_HOST, SAP_PORT), timeout=15) as raw:
        with ctx_none.wrap_socket(raw, server_hostname=SAP_HOST) as ssock:
            # Get leaf cert in DER
            leaf_der = ssock.getpeercert(binary_form=True)
            print(f"  Leaf cert DER length: {len(leaf_der)} bytes")

            # Try to access the full chain via internal methods
            sslobj = ssock._sslobj
            print(f"  SSLObject methods: {[m for m in dir(sslobj) if 'cert' in m.lower() or 'chain' in m.lower() or 'peer' in m.lower()]}")

            # Python 3.10+: try get_verified_chain / get_unverified_chain
            for method_name in ['get_verified_chain', 'get_unverified_chain', 'peer_certificate']:
                method = getattr(sslobj, method_name, None)
                if method:
                    try:
                        result = method()
                        print(f"  {method_name}() returned: {type(result)} len={len(result) if hasattr(result, '__len__') else 'N/A'}")
                    except Exception as e:
                        print(f"  {method_name}() error: {e}")

            # Save DER to file for later analysis
            with open("sap_leaf_cert.der", "wb") as f:
                f.write(leaf_der)
            print(f"  Saved leaf cert DER to sap_leaf_cert.der")

            # Convert DER to PEM
            leaf_pem = "-----BEGIN CERTIFICATE-----\n"
            leaf_pem += base64.encodebytes(leaf_der).decode()
            leaf_pem += "-----END CERTIFICATE-----\n"
            with open("sap_leaf_from_socket.pem", "w") as f:
                f.write(leaf_pem)
            print(f"  Saved leaf cert PEM to sap_leaf_from_socket.pem")

except Exception as e:
    print(f"  Error: {e}")

print()

# Step 2: Try httpx with the CA bundle
print("Step 2: Testing httpx with CA bundle...")
try:
    import httpx
    url = f"https://{SAP_HOST}:{SAP_PORT}/sap/opu/odata/sap/ZNCOREIQ_REPO_SRV/SystemInfoSet"

    try:
        r = httpx.get(url, verify=CA_BUNDLE, timeout=15,
                      params={"sap-client": "800", "$format": "json"})
        print(f"  HTTP status: {r.status_code} — SUCCESS")
    except httpx.SSLError as e:
        print(f"  SSL Error: {e}")
    except Exception as e:
        print(f"  Other Error: {type(e).__name__}: {e}")
except ImportError:
    print("  httpx not available")

print()

# Step 3: Try httpx with VERIFY = leaf cert loaded differently
print("Step 3: Testing ssl context with leaf cert as verify cert...")
try:
    import httpx

    # Build ssl context that trusts our pem
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_REQUIRED
    ssl_ctx.load_verify_locations(CA_BUNDLE)

    url = f"https://{SAP_HOST}:{SAP_PORT}/sap/opu/odata/sap/ZNCOREIQ_REPO_SRV/SystemInfoSet"
    try:
        r = httpx.get(url, verify=ssl_ctx, timeout=15,
                      params={"sap-client": "800", "$format": "json"})
        print(f"  HTTP status: {r.status_code} — SUCCESS with custom ctx")
    except httpx.SSLError as e:
        print(f"  SSL Error: {e}")
    except Exception as e:
        print(f"  Other Error: {type(e).__name__}: {e}")
except ImportError:
    print("  httpx not available")
