#!/usr/bin/env bash
#
# Copy S3 asset bucket to the same region as the EC2 instance and point the app at it.
# Run on the stage server (or any host with access to .env and AWS CLI).
#
# Usage (on server; app user in .env often lacks CreateBucket - use migration user):
#   export MIGRATION_AWS_ACCESS_KEY_ID=AKIA...
#   export MIGRATION_AWS_SECRET_ACCESS_KEY=...
#   ENV_FILE=/home/ubuntu/composer.stori.audio/.env ./scripts/deploy/s3-sync-to-instance-region.sh
#
# Or from repo root with only .env (fails if app user cannot create buckets):
#   ./scripts/deploy/s3-sync-to-instance-region.sh
#
set -euo pipefail

ENV_FILE="${ENV_FILE:-}"
if [[ -z "$ENV_FILE" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  ENV_FILE="$(cd "$SCRIPT_DIR/../.." && pwd)/.env"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env not found at $ENV_FILE. Set ENV_FILE or run from repo root."
  exit 1
fi

# Load Stori bucket/region from .env (required for source bucket name)
set -a
source <(grep -E '^(STORI_AWS_S3_ASSET_BUCKET|STORI_AWS_REGION)=' "$ENV_FILE" | sed 's/^/export /')
set +a

# Use migration credentials for create/sync if set; otherwise use .env (app user)
if [[ -n "${MIGRATION_AWS_ACCESS_KEY_ID:-}" && -n "${MIGRATION_AWS_SECRET_ACCESS_KEY:-}" ]]; then
  export AWS_ACCESS_KEY_ID="$MIGRATION_AWS_ACCESS_KEY_ID"
  export AWS_SECRET_ACCESS_KEY="$MIGRATION_AWS_SECRET_ACCESS_KEY"
  echo "Using MIGRATION_* credentials for create/sync."
else
  set -a
  source <(grep -E '^(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY)=' "$ENV_FILE" | sed 's/^/export /')
  set +a
fi

SOURCE_BUCKET="${STORI_AWS_S3_ASSET_BUCKET:?Set STORI_AWS_S3_ASSET_BUCKET in .env}"
TARGET_REGION="us-east-2"   # same as stage instance

if ! command -v aws &>/dev/null; then
  echo "Installing AWS CLI v2..."
  curl -sSfL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
  unzip -q -o /tmp/awscliv2.zip -d /tmp
  sudo /tmp/aws/install -i /usr/local/aws-cli -b /usr/local/bin 2>/dev/null || true
  rm -rf /tmp/awscliv2.zip /tmp/aws
  command -v aws &>/dev/null || { echo "ERROR: AWS CLI install failed"; exit 1; }
fi

echo "Checking AWS credentials..."
export AWS_DEFAULT_REGION="${STORI_AWS_REGION:-us-east-1}"
if ! aws sts get-caller-identity &>/dev/null; then
  echo "ERROR: AWS credentials are invalid or insufficient."
  echo "  For create/sync use: export MIGRATION_AWS_ACCESS_KEY_ID=... MIGRATION_AWS_SECRET_ACCESS_KEY=..."
  exit 1
fi

# Resolve source bucket region (API returns null for us-east-1)
SOURCE_REGION=$(aws s3api get-bucket-location --bucket "$SOURCE_BUCKET" --query 'LocationConstraint' --output text 2>/dev/null || true)
if [[ "$SOURCE_REGION" == "None" || -z "$SOURCE_REGION" ]]; then
  SOURCE_REGION="us-east-1"
fi

echo "Source bucket: $SOURCE_BUCKET (region: $SOURCE_REGION)"
echo "Target region: $TARGET_REGION (instance region)"

if [[ "$SOURCE_REGION" == "$TARGET_REGION" ]]; then
  echo "Bucket is already in instance region. No sync needed."
  echo "Ensure STORI_AWS_REGION=$TARGET_REGION in .env. Current: STORI_AWS_REGION=${STORI_AWS_REGION:-unset}"
  exit 0
fi

# New bucket name in target region (S3 bucket names are global; use suffix to avoid conflict)
TARGET_BUCKET="${SOURCE_BUCKET}-${TARGET_REGION//[.-]/}"
echo "Target bucket (new): $TARGET_BUCKET"

echo "Creating bucket $TARGET_BUCKET in $TARGET_REGION..."
if aws s3api head-bucket --bucket "$TARGET_BUCKET" 2>/dev/null; then
  echo "  Bucket already exists."
else
  aws s3api create-bucket --bucket "$TARGET_BUCKET" --region "$TARGET_REGION" --create-bucket-configuration "LocationConstraint=$TARGET_REGION"
fi

echo "Syncing objects from s3://$SOURCE_BUCKET to s3://$TARGET_BUCKET..."
aws s3 sync "s3://$SOURCE_BUCKET" "s3://$TARGET_BUCKET" --source-region "$SOURCE_REGION" --region "$TARGET_REGION"

echo "Updating .env..."
# Backup
cp "$ENV_FILE" "${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
# Update bucket and region
sed -i "s|^STORI_AWS_S3_ASSET_BUCKET=.*|STORI_AWS_S3_ASSET_BUCKET=$TARGET_BUCKET|" "$ENV_FILE"
sed -i "s|^STORI_AWS_REGION=.*|STORI_AWS_REGION=$TARGET_REGION|" "$ENV_FILE"
echo "  STORI_AWS_S3_ASSET_BUCKET=$TARGET_BUCKET"
echo "  STORI_AWS_REGION=$TARGET_REGION"

echo "Restarting composer (docker compose)..."
cd "$(dirname "$ENV_FILE")"
docker compose restart composer 2>/dev/null || docker-compose restart composer 2>/dev/null || echo "  (restart manually: cd $(dirname "$ENV_FILE") && docker compose restart composer)"

echo "Done. Assets are now served from $TARGET_BUCKET in $TARGET_REGION."
echo ""
echo "IMPORTANT: Add the new bucket to the app IAM user (stori-assets-app) so the app can generate presigned URLs:"
echo "  IAM -> Users -> stori-assets-app -> Add permissions -> attach policy or edit inline policy."
echo "  Add s3:GetObject and s3:ListBucket for: arn:aws:s3:::${TARGET_BUCKET} and arn:aws:s3:::${TARGET_BUCKET}/*"
