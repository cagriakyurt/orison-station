#!/bin/bash
# scripts/generate_certs.sh: Generate custom CA and server SSL certificate for station.local
set -e

BASE_DIR="/home/host/station"
SSL_DIR="$BASE_DIR/ssl"
mkdir -p "$SSL_DIR"
cd "$SSL_DIR"

if [ -f "server.crt" ] && [ -f "server.key" ]; then
    echo "SSL certificates already exist in $SSL_DIR"
    exit 0
fi

echo "=== Generating Custom Root CA ==="
# Generate CA private key
openssl genrsa -out localCA.key 2048
# Generate CA certificate
openssl req -x509 -new -nodes -key localCA.key -sha256 -days 3650 -out localCA.pem -subj "/CN=Orison Local CA/O=Orison/C=TR"

echo "=== Generating Server Certificate ==="
# Generate server private key
openssl genrsa -out server.key 2048

# Create server config extensions file
cat > server.ext << EOF
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage = digitalSignature, nonRepudiation, keyEncipherment, dataEncipherment
subjectAltName = @alt_names

[alt_names]
DNS.1 = station.local
DNS.2 = localhost
IP.1 = 127.0.0.1
EOF

# Generate Certificate Signing Request (CSR)
openssl req -new -key server.key -out server.csr -subj "/CN=station.local/O=Orison/C=TR"

# Sign server certificate with custom root CA
openssl x509 -req -in server.csr -CA localCA.pem -CAkey localCA.key -CAcreateserial -out server.crt -days 3650 -sha256 -extfile server.ext

# Clean up CSR and serial file
rm -f server.csr localCA.srl server.ext

# Copy root CA to static directory so Flask can serve it if needed
mkdir -p "$BASE_DIR/web/static"
cp localCA.pem "$BASE_DIR/web/static/localCA.pem"

echo "=== SSL Certificates Generated Successfully ==="
