#!/bin/bash
# Production deployment script with security checks

set -e

echo "=========================================="
echo "Stori Production Deployment"
echo "=========================================="
echo ""

# Check if running as the right user
if [ "$EUID" -eq 0 ]; then 
    echo "ERROR: Don't run as root. Run as ubuntu user."
    exit 1
fi

# Navigate to project root (script is in scripts/deploy/)
cd "$(dirname "$0")/../.."

echo "1. Security pre-flight checks..."

# Check .env exists and has required vars
if [ ! -f .env ]; then
    echo "❌ ERROR: .env file not found"
    echo "   Copy .env.example to .env and configure"
    exit 1
fi

# Check CORS is not wildcard
if grep -q "STORI_CORS_ORIGINS=\*" .env 2>/dev/null; then
    echo "⚠️  WARNING: CORS is set to wildcard (*) in .env"
    echo "   This is a SECURITY RISK in production!"
    echo "   Set STORI_CORS_ORIGINS=https://stage.stori.audio,stori://"
    read -p "   Continue anyway? (yes/no): " continue
    if [ "$continue" != "yes" ]; then
        exit 1
    fi
fi

# Check secrets are set
if grep -q "your_.*_key_here" .env 2>/dev/null; then
    echo "❌ ERROR: Placeholder secrets found in .env"
    echo "   Replace all 'your_*_key_here' values with real keys"
    exit 1
fi

echo "✅ Pre-flight checks passed"

echo ""
echo "2. Pulling latest code..."
# If you have git on the server, use: git pull
# Otherwise files should already be scp'd

echo ""
echo "3. Building Docker images..."
docker compose build --no-cache

echo ""
echo "4. Stopping old containers..."
docker compose down

echo ""
echo "5. Starting new containers..."
docker compose up -d

echo ""
echo "6. Waiting for services to be healthy..."
sleep 10

# Check health
for i in {1..30}; do
    if docker exec maestro-stori-app curl -sf http://localhost:10001/api/v1/health > /dev/null 2>&1; then
        echo "✅ Maestro healthy"
        break
    fi
    echo "   Waiting for maestro... ($i/30)"
    sleep 2
done

for i in {1..30}; do
    if docker exec maestro-stori-orpheus curl -sf http://localhost:10002/health > /dev/null 2>&1; then
        echo "✅ Orpheus healthy"
        break
    fi
    echo "   Waiting for orpheus... ($i/30)"
    sleep 2
done

echo ""
echo "7. Verifying external access..."
if curl -sf https://stage.stori.audio/api/v1/health > /dev/null 2>&1; then
    echo "✅ External API accessible"
else
    echo "⚠️  External API check failed - check nginx logs"
fi

echo ""
echo "8. Container status:"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo ""
echo "=========================================="
echo "✅ Deployment Complete!"
echo "=========================================="
echo ""
echo "Monitor logs:"
echo "  docker logs -f maestro-stori-app"
echo "  docker logs -f maestro-stori-orpheus"
echo "  docker logs -f maestro-stori-nginx"
echo ""
echo "Check status:"
echo "  docker ps"
echo "  curl https://stage.stori.audio/api/v1/health"
echo ""
