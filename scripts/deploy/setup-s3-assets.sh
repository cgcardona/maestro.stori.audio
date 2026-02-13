#!/usr/bin/env bash
#
# Create S3 bucket and IAM user for on-demand asset delivery (drum kits, soundfont).
# Run with AWS credentials that can create S3 buckets and IAM users (e.g. admin or
# credentials with s3:*, iam:CreateUser, iam:PutUserPolicy, iam:CreateAccessKey).
#
# Usage:
#   # From your machine (with AWS CLI and profile/keys set):
#   ./deploy/setup-s3-assets.sh
#
#   # From the server via SSH (install CLI first, then run with env vars):
#   ssh ubuntu@18.216.132.182
#   # One-time: install AWS CLI v2 if missing
#   sudo apt-get update && sudo apt-get install -y awscli
#   # Or install AWS CLI v2 (recommended):
#   # curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip && unzip -q /tmp/awscliv2.zip -d /tmp && sudo /tmp/aws/install
#   export AWS_ACCESS_KEY_ID=AKIA...
#   export AWS_SECRET_ACCESS_KEY=...
#   export AWS_DEFAULT_REGION=us-east-1
#   ./deploy/setup-s3-assets.sh
#
# Options (env or arguments):
#   BUCKET_NAME   S3 bucket name (default: stori-assets)
#   REGION        AWS region (default: us-east-1)
#   IAM_USER      IAM user name for the app (default: stori-assets-app)
#
# Output: env vars to add to your server .env or Docker for the composer service.
#
set -euo pipefail

BUCKET_NAME="${BUCKET_NAME:-stori-assets}"
REGION="${REGION:-us-east-1}"
IAM_USER="${IAM_USER:-stori-assets-app}"

# Optional: override via first args
if [[ "${1:-}" != "" ]]; then BUCKET_NAME="$1"; fi
if [[ "${2:-}" != "" ]]; then REGION="$2"; fi

export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-$REGION}"

command -v aws >/dev/null 2>&1 || {
  echo "ERROR: AWS CLI not found. Install it first:"
  echo "  Ubuntu: sudo apt-get update && sudo apt-get install -y awscli"
  echo "  Or AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
  exit 1
}

echo "Checking AWS credentials..."
if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo "ERROR: AWS credentials not configured or invalid."
  echo "  Set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and AWS_DEFAULT_REGION (or run 'aws configure')."
  exit 1
fi
echo "  Caller: $(aws sts get-caller-identity --query Arn --output text)"
echo ""

echo "Creating S3 bucket: $BUCKET_NAME (region: $REGION)"
if aws s3api head-bucket --bucket "$BUCKET_NAME" 2>/dev/null; then
  echo "  Bucket already exists."
else
  if [[ "$REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET_NAME"
  else
    aws s3api create-bucket --bucket "$BUCKET_NAME" --create-bucket-configuration "LocationConstraint=$REGION"
  fi
  echo "  Created."
fi

echo "Blocking public access on bucket..."
aws s3api put-public-access-block \
  --bucket "$BUCKET_NAME" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
echo "  Done."

echo "Creating IAM user: $IAM_USER (for app credentials)"
if aws iam get-user --user-name "$IAM_USER" >/dev/null 2>&1; then
  echo "  User already exists."
else
  aws iam create-user --user-name "$IAM_USER"
  echo "  Created."
fi

POLICY_DOC=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:HeadObject"
      ],
      "Resource": "arn:aws:s3:::${BUCKET_NAME}/*"
    },
    {
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::${BUCKET_NAME}",
      "Condition": {
        "StringLike": { "s3:prefix": ["assets/*"] }
      }
    }
  ]
}
EOF
)

echo "Attaching inline policy to $IAM_USER..."
aws iam put-user-policy \
  --user-name "$IAM_USER" \
  --policy-name "stori-assets-s3-read" \
  --policy-document "$POLICY_DOC"
echo "  Done."

echo "Creating access key for $IAM_USER..."
# Max 2 access keys per user; if this fails, use existing keys or delete one in IAM
KEY_OUTPUT=$(aws iam create-access-key --user-name "$IAM_USER" 2>/dev/null) || {
  echo "  User already has 2 access keys. Use existing credentials or delete one:"
  echo "    aws iam list-access-keys --user-name $IAM_USER"
  echo "    aws iam delete-access-key --user-name $IAM_USER --access-key-id <ID>"
  exit 1
}
ACCESS_KEY=$(echo "$KEY_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['AccessKey']['AccessKeyId'])")
SECRET_KEY=$(echo "$KEY_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['AccessKey']['SecretAccessKey'])")
echo "  Created access key: $ACCESS_KEY"
echo ""

echo "============================================================"
echo "Add these to the project root .env (same dir as docker-compose.yml):"
echo "Then restart the composer container so it gets the vars."
echo "============================================================"
echo ""
echo "# AWS S3 Asset Delivery (composer container reads these)"
echo "STORI_AWS_S3_ASSET_BUCKET=$BUCKET_NAME"
echo "STORI_AWS_REGION=$REGION"
echo "STORI_PRESIGN_EXPIRY_SECONDS=3600"
echo ""
echo "# App IAM user credentials (boto3 reads these in the container)"
echo "AWS_ACCESS_KEY_ID=$ACCESS_KEY"
echo "AWS_SECRET_ACCESS_KEY=$SECRET_KEY"
echo ""
echo "============================================================"
echo "Then restart composer so the container gets the new env:"
echo "  cd ~/composer.stori.audio && docker compose up -d composer"
echo ""
echo "Upload assets:"
echo "  python scripts/upload_assets_to_s3.py /path/to/assets_source --bucket $BUCKET_NAME --region $REGION"
echo "============================================================"
