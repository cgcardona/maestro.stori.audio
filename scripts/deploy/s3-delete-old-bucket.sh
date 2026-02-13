#!/usr/bin/env bash
#
# Empty and delete the old S3 asset bucket after migration to the instance-region bucket.
# Requires credentials with s3:DeleteObject and s3:DeleteBucket (e.g. MIGRATION_* or admin).
#
# Usage (on server, with migration user creds):
#   export MIGRATION_AWS_ACCESS_KEY_ID=...
#   export MIGRATION_AWS_SECRET_ACCESS_KEY=...
#   bash scripts/deploy/s3-delete-old-bucket.sh
#
# Or with explicit bucket/region:
#   OLD_BUCKET=stori-assets-992382692655 OLD_REGION=eu-west-1 bash scripts/deploy/s3-delete-old-bucket.sh
#
set -euo pipefail

OLD_BUCKET="${OLD_BUCKET:-stori-assets-992382692655}"
OLD_REGION="${OLD_REGION:-eu-west-1}"

if [[ -n "${MIGRATION_AWS_ACCESS_KEY_ID:-}" && -n "${MIGRATION_AWS_SECRET_ACCESS_KEY:-}" ]]; then
  export AWS_ACCESS_KEY_ID="$MIGRATION_AWS_ACCESS_KEY_ID"
  export AWS_SECRET_ACCESS_KEY="$MIGRATION_AWS_SECRET_ACCESS_KEY"
fi

export AWS_DEFAULT_REGION="$OLD_REGION"

if ! command -v aws &>/dev/null; then
  echo "ERROR: AWS CLI not found. Install it or run from a host that has it."
  exit 1
fi

echo "Checking credentials..."
if ! aws sts get-caller-identity &>/dev/null; then
  echo "ERROR: AWS credentials invalid. Set MIGRATION_AWS_ACCESS_KEY_ID and MIGRATION_AWS_SECRET_ACCESS_KEY."
  exit 1
fi

if ! aws s3api head-bucket --bucket "$OLD_BUCKET" 2>/dev/null; then
  echo "Bucket $OLD_BUCKET does not exist or you don't have access. Nothing to delete."
  exit 0
fi

echo "Emptying bucket: s3://$OLD_BUCKET (region: $OLD_REGION)..."
aws s3 rm "s3://$OLD_BUCKET" --recursive --region "$OLD_REGION"

echo "Deleting bucket: $OLD_BUCKET..."
if ! aws s3api delete-bucket --bucket "$OLD_BUCKET" --region "$OLD_REGION"; then
  echo "If the bucket has versioning enabled, empty it in the AWS Console (S3 -> bucket -> Empty), then run:"
  echo "  aws s3api delete-bucket --bucket $OLD_BUCKET --region $OLD_REGION"
  exit 1
fi

echo "Done. Old bucket $OLD_BUCKET has been deleted."
echo "You can remove the old bucket from the stori-assets-app IAM policy (optional)."
