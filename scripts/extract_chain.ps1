# Extract full TLS certificate chain from SAP Gateway
# Writes all certs (leaf + intermediates + root) to sap_ca.pem

$host_name = "HYDRSAPT02.AMERICAS.GLOBAL.NTTDATA.COM"
$port = 44300
$outFile = "c:\sap-repository-intelligence\sap_ca.pem"

Write-Host "Connecting to ${host_name}:${port} ..."

try {
    $tcpClient = New-Object System.Net.Sockets.TcpClient
    $tcpClient.Connect($host_name, $port)

    $callback = [System.Net.Security.RemoteCertificateValidationCallback] { $true }
    $sslStream = New-Object System.Net.Security.SslStream(
        $tcpClient.GetStream(),
        $false,
        $callback
    )
    $sslStream.AuthenticateAsClient($host_name)

    Write-Host "TLS handshake successful"
    Write-Host ("Negotiated protocol: " + $sslStream.SslProtocol)

    $cert = $sslStream.RemoteCertificate
    Write-Host ("Server cert subject: " + $cert.Subject)
    Write-Host ("Server cert issuer:  " + $cert.Issuer)

    # Build the chain - this uses Windows cert store + server-provided certs
    $chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
    $chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
    $chain.ChainPolicy.VerificationFlags = [System.Security.Cryptography.X509Certificates.X509VerificationFlags]::AllFlags
    $built = $chain.Build($cert)

    Write-Host ("Chain built: " + $built)
    Write-Host ("Chain length: " + $chain.ChainElements.Count)

    $allPem = ""
    $i = 0
    foreach ($el in $chain.ChainElements) {
        $c = $el.Certificate
        Write-Host ("  [$i] Subject: " + $c.Subject)
        Write-Host ("      Issuer:  " + $c.Issuer)
        Write-Host ("      Thumbprint: " + $c.Thumbprint)
        $b64 = [Convert]::ToBase64String($c.RawData, "InsertLineBreaks")
        $allPem += "-----BEGIN CERTIFICATE-----`n" + $b64 + "`n-----END CERTIFICATE-----`n"
        $i++
    }

    if ($allPem -ne "") {
        [System.IO.File]::WriteAllText($outFile, $allPem)
        Write-Host "`nWrote $i certificate(s) to $outFile"
    } else {
        Write-Host "ERROR: No certificates extracted"
    }

    $sslStream.Close()
    $tcpClient.Close()
}
catch {
    Write-Host ("ERROR: " + $_.Exception.Message)
    Write-Host $_.ScriptStackTrace
}
