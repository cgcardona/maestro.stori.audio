#!/usr/bin/env python3
"""
Upload minimal placeholder drum kit zips to S3 so download URLs return 200.

Each zip contains only kit.json (no WAVs). Use for staging or until real assets
are uploaded with upload_assets_to_s3.py.

kit.json schema: name, author, sounds, license, version (see ASSETS_API.md).

Requires AWS credentials with s3:PutObject on the bucket (e.g. stori-admin or
stori-assets-app with PutObject added).

Usage:
  AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \\
  python scripts/upload_placeholder_kits.py --bucket BUCKET --region REGION

Environment:
  ASSET_AUTHOR  Optional; default "Maestro"
  ASSET_LICENSE Optional; default "CC0"
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DRUM_KITS_PREFIX = "assets/drum-kits/"
KITS = [
    ("cr78", "CR-78"),
    ("linndrum", "LinnDrum"),
    ("pearl", "Pearl"),
    ("tr505", "TR-505"),
    ("tr909", "TR-909"),
    ("template", "Template Kit"),
]


def _asset_author() -> str:
    return os.environ.get("ASSET_AUTHOR", "Maestro")


def _asset_license() -> str:
    return os.environ.get("ASSET_LICENSE", "CC0")


def kit_json_payload(kit_id: str, name: str) -> dict:
    """Build kit.json dict matching app schema: name, author, sounds, license, version."""
    return {
        "name": name,
        "author": _asset_author(),
        "license": _asset_license(),
        "version": "1.0",
        "sounds": {},  # Placeholder: no WAVs; real kits map sound type -> filename
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Upload placeholder drum kit zips to S3")
    p.add_argument("--bucket", "-b", required=True, help="S3 bucket name")
    p.add_argument("--region", "-r", default="eu-west-1", help="AWS region")
    args = p.parse_args()

    s3 = boto3.client("s3", region_name=args.region)
    bucket = args.bucket

    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as e:
        logger.error("Cannot access bucket %s: %s", bucket, e)
        sys.exit(1)

    for kit_id, name in KITS:
        payload = kit_json_payload(kit_id, name)
        kit_json = json.dumps(payload, indent=2)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("kit.json", kit_json)
        buf.seek(0)
        key = f"{DRUM_KITS_PREFIX}{kit_id}.zip"
        s3.upload_fileobj(buf, bucket, key, ExtraArgs={"ContentType": "application/zip"})
        logger.info("Uploaded %s", key)

    manifest = {"kits": [{"id": k[0], "name": k[1], "version": "1.0"} for k in KITS]}
    s3.put_object(
        Bucket=bucket,
        Key=f"{DRUM_KITS_PREFIX}manifest.json",
        Body=json.dumps(manifest, indent=2).encode(),
        ContentType="application/json",
    )
    logger.info("Uploaded %smanifest.json", DRUM_KITS_PREFIX)
    logger.info("Done.")


if __name__ == "__main__":
    main()
