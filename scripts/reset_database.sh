#!/bin/bash
set -e

echo "🗑️  Stori Composer Database Reset Script"
echo "========================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Project root (script lives in scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Load environment variables
if [ -f "$PROJECT_ROOT/.env" ]; then
    echo "📋 Loading environment variables..."
    export $(grep -v '^#' "$PROJECT_ROOT/.env" | xargs)
fi

# Detect database type
if [[ "$DATABASE_URL" == *"postgresql"* ]]; then
    DB_TYPE="postgres"
    echo "🐘 Detected PostgreSQL database"
elif [[ "$DATABASE_URL" == *"sqlite"* ]]; then
    DB_TYPE="sqlite"
    echo "📁 Detected SQLite database"
else
    echo -e "${YELLOW}⚠️  Could not detect database type from DATABASE_URL${NC}"
    echo "Please ensure DATABASE_URL is set in your .env file"
    exit 1
fi

echo ""
echo -e "${RED}⚠️  WARNING: This will DELETE ALL DATA in your database!${NC}"
echo ""
read -p "Are you sure you want to continue? (type 'yes' to confirm): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "🛑 Step 1: Stopping services..."
docker-compose -f "$PROJECT_ROOT/docker-compose.yml" down || true

echo ""
echo "🗑️  Step 2: Removing old database..."

if [ "$DB_TYPE" = "sqlite" ]; then
    # Extract database file path from DATABASE_URL
    # Format: sqlite+aiosqlite:///path/to/db.sqlite
    DB_FILE=$(echo "$DATABASE_URL" | sed 's|sqlite+aiosqlite:///||' | sed 's|sqlite:///||')
    
    if [ -f "$DB_FILE" ]; then
        rm "$DB_FILE"
        echo "✅ Removed SQLite database: $DB_FILE"
    else
        echo "ℹ️  Database file not found: $DB_FILE"
    fi
    
elif [ "$DB_TYPE" = "postgres" ]; then
    echo "🐘 Dropping and recreating PostgreSQL database..."
    
    # Extract database connection details
    # This is a simplified approach - you may need to adjust based on your setup
    docker-compose -f "$PROJECT_ROOT/docker-compose.yml" up -d postgres
    sleep 3
    
    # Drop and recreate database
    docker-compose -f "$PROJECT_ROOT/docker-compose.yml" exec -T postgres psql -U postgres -c "DROP DATABASE IF EXISTS stori;" || true
    docker-compose -f "$PROJECT_ROOT/docker-compose.yml" exec -T postgres psql -U postgres -c "CREATE DATABASE stori OWNER stori;"
    
    echo "✅ Database reset complete"
fi

echo ""
echo "🗑️  Step 3: Clearing Alembic version history..."
rm -rf alembic/versions/__pycache__ 2>/dev/null || true

echo ""
echo "📦 Step 4: Running migrations..."
echo ""

# Run migrations
alembic upgrade head

echo ""
echo -e "${GREEN}✅ Database reset complete!${NC}"
echo ""
echo "📊 Current schema:"
alembic current

echo ""
echo "🚀 Next steps:"
echo "   1. Start your services: docker-compose up -d"
echo "   2. Create a test user: python generate_test_token.py"
echo ""
