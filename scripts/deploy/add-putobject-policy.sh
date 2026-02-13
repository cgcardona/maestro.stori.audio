#!/usr/bin/env bash
# Add s3:PutObject to stori-assets-app so the app (or upload scripts) can upload to the bucket.
# Run with AWS credentials that have iam:PutUserPolicy (e.g. stori-admin).
# Do NOT use the server .env (that has app credentials). Use admin credentials instead.
#
# Usage:
#   From your machine (with AWS CLI and admin profile):
#     AWS_PROFILE=your-admin bash deploy/add-putobject-policy.sh [BUCKET_NAME]
#   From the server (paste admin credentials first):
#     export AWS_ACCESS_KEY_ID=AKIA... AWS_SECRET_ACCESS_KEY=... AWS_DEFAULT_REGION=eu-west-1
#     bash deploy/add-putobject-policy.sh [BUCKET_NAME]
set -euo pipefail
BUCKET_NAME="${1:-stori-assets-992382692655}"
IAM_USER="${IAM_USER:-stori-assets-app}"

if ! aws sts get-caller-identity &>/dev/null; then
  echo "ERROR: No AWS credentials found. This script needs IAM permissions (e.g. stori-admin)."
  echo "  On the server, do NOT use .env (it has app credentials). Instead run:"
  echo "    export AWS_ACCESS_KEY_ID=<admin-key> AWS_SECRET_ACCESS_KEY=<admin-secret> AWS_DEFAULT_REGION=eu-west-1"
  echo "  Or run this script from your machine with: AWS_PROFILE=admin bash deploy/add-putobject-policy.sh $BUCKET_NAME"
  exit 1
fi

POLICY_DOC=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:HeadObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::${BUCKET_NAME}/*"
    },
    {
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::${BUCKET_NAME}",
      "Condition": { "StringLike": { "s3:prefix": ["assets/*"] } }
    }
  ]
}
EOF
)

echo "Updating IAM user $IAM_USER policy to add s3:PutObject on $BUCKET_NAME..."
aws iam put-user-policy \
  --user-name "$IAM_USER" \
  --policy-name "stori-assets-s3-read" \
  --policy-document "$POLICY_DOC"
echo "Done. You can now upload placeholder kits from the server (e.g. docker exec ... python scripts/upload_placeholder_kits.py ...)."
