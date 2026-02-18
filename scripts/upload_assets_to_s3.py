#!/usr/bin/env python3
"""
Upload drum kits and GM soundfont to S3 for on-demand asset delivery.

Expects a local directory with this layout:
  SOURCE_DIR/
    drum-kits/
      cr78/kit.json, cr78/*.wav
      linndrum/kit.json, linndrum/*.wav
      pearl/, tr505/, tr909/, template/
    soundfonts/
      MuseScore_General.sf2  (or equivalent)

Creates in S3:
  - assets/drum-kits/{kit_id}.zip (one zip per kit: kit.json + all .wav)
  - assets/drum-kits/manifest.json
  - assets/drum-kits/{kit_id}/kit.json, {kit_id}/*.wav (optional; for reference)
  - assets/soundfonts/{filename}.sf2
  - assets/soundfonts/manifest.json
  - assets/bundle/all-assets.zip (optional: all kits + soundfont)

Usage:
  python scripts/upload_assets_to_s3.py SOURCE_DIR [--bucket BUCKET] [--region REGION] [--no-bundle]

Environment:
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (or IAM role)
  STORI_AWS_S3_ASSET_BUCKET (or --bucket)
  STORI_AWS_REGION (or --region)
  STORI_ASSET_AUTHOR  Optional; default "Stori Maestro" (used when kit.json omits author)
  STORI_ASSET_LICENSE Optional; default "CC0" (used when kit.json omits license)
"""
import argparse
import json
import os
import sys
import zipfile
from pathlib import Path

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import boto3
from botocore.exceptions import ClientError

# S3 key prefixes (must match app/services/assets.py)
DRUM_KITS_PREFIX = "assets/drum-kits/"
DRUM_KITS_MANIFEST_KEY = "assets/drum-kits/manifest.json"
SOUNDFONTS_PREFIX = "assets/soundfonts/"
SOUNDFONTS_MANIFEST_KEY = "assets/soundfonts/manifest.json"
BUNDLE_KEY = "assets/bundle/all-assets.zip"


def get_s3_client(region: str):
    return boto3.client("s3", region_name=region)


def normalize_kit_meta(meta: dict, kit_id: str) -> dict:
    """Ensure kit.json has name, author, sounds, license, version (see ASSETS_API.md)."""
    out = dict(meta)
    out.setdefault("name", kit_id)
    out.setdefault("author", os.environ.get("STORI_ASSET_AUTHOR", "Stori Maestro"))
    out.setdefault("license", os.environ.get("STORI_ASSET_LICENSE", "CC0"))
    out.setdefault("version", "1.0")
    out.setdefault("sounds", {})
    return out


def build_kit_zip(kit_dir: Path, kit_json_override: dict | None = None) -> bytes:
    """Build zip of kit_dir (kit.json + all .wav). If kit_json_override is set, use it for kit.json."""
    buf = __import__("io").BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if kit_json_override is not None:
            zf.writestr("kit.json", json.dumps(kit_json_override, indent=2))
        for f in kit_dir.rglob("*"):
            if f.is_file():
                arcname = f.relative_to(kit_dir)
                if arcname.name == "kit.json" and kit_json_override is not None:
                    continue  # already added
                zf.write(f, arcname)
    return buf.getvalue()


def upload_drum_kits(client, bucket: str, source_dir: Path) -> list[dict]:
    drum_kits_dir = source_dir / "drum-kits"
    if not drum_kits_dir.is_dir():
        print("No drum-kits/ directory found, skipping.")
        return []
    manifest_kits = []
    for kit_dir in sorted(drum_kits_dir.iterdir()):
        if not kit_dir.is_dir():
            continue
        kit_id = kit_dir.name
        kit_json = kit_dir / "kit.json"
        if not kit_json.exists():
            print(f"  Skip {kit_id}: no kit.json")
            continue
        try:
            with open(kit_json) as f:
                meta = json.load(f)
            meta = normalize_kit_meta(meta, kit_id)
            name = meta["name"]
            version = meta["version"]
        except Exception as e:
            print(f"  Skip {kit_id}: invalid kit.json - {e}")
            continue
        # Zip and upload (kit.json in zip uses normalized meta: author, license, sounds, etc.)
        zip_bytes = build_kit_zip(kit_dir, kit_json_override=meta)
        key_zip = f"{DRUM_KITS_PREFIX}{kit_id}.zip"
        from io import BytesIO
        client.upload_fileobj(
            BytesIO(zip_bytes), bucket, key_zip,
            ExtraArgs={"ContentType": "application/zip"},
        )
        print(f"  Uploaded {kit_id}.zip -> s3://{bucket}/{key_zip}")
        file_count = len(list(kit_dir.rglob("*.wav"))) + (1 if kit_json.exists() else 0)
        manifest_kits.append({
            "id": kit_id,
            "name": name,
            "version": version,
            "fileCount": file_count,
        })
    return manifest_kits


def upload_soundfonts(client, bucket: str, source_dir: Path) -> list[dict]:
    soundfonts_dir = source_dir / "soundfonts"
    if not soundfonts_dir.is_dir():
        print("No soundfonts/ directory found, skipping.")
        return []
    manifest_sf = []
    for f in sorted(soundfonts_dir.iterdir()):
        if not f.is_file() or f.suffix.lower() != ".sf2":
            continue
        filename = f.name
        sf_id = f.stem.replace(" ", "_").lower()
        name = f.stem.replace("_", " ")
        key = f"{SOUNDFONTS_PREFIX}{filename}"
        client.upload_file(str(f), bucket, key, ExtraArgs={"ContentType": "application/octet-stream"})
        print(f"  Uploaded {filename} -> s3://{bucket}/{key}")
        manifest_sf.append({"id": sf_id, "name": name, "filename": filename})
    return manifest_sf


def upload_manifests(client, bucket: str, drum_kits: list[dict], soundfonts: list[dict]):
    if drum_kits:
        body = json.dumps({"kits": drum_kits}, indent=2)
        client.put_object(
            Bucket=bucket,
            Key=DRUM_KITS_MANIFEST_KEY,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
        print(f"  Uploaded manifest -> s3://{bucket}/{DRUM_KITS_MANIFEST_KEY}")
    if soundfonts:
        body = json.dumps({"soundfonts": soundfonts}, indent=2)
        client.put_object(
            Bucket=bucket,
            Key=SOUNDFONTS_MANIFEST_KEY,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
        print(f"  Uploaded manifest -> s3://{bucket}/{SOUNDFONTS_MANIFEST_KEY}")


def build_bundle_zip(source_dir: Path, kit_ids: list[str]) -> bytes:
    """Build zip containing drum-kits/*.zip (as kit_id/) and soundfonts/*.sf2."""
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add each kit as drum-kits/{kit_id}/ (contents of that kit folder)
        drum_kits_dir = source_dir / "drum-kits"
        for kit_id in kit_ids:
            kit_dir = drum_kits_dir / kit_id
            if not kit_dir.is_dir():
                continue
            for f in kit_dir.rglob("*"):
                if f.is_file():
                    arcname = f"drum-kits/{kit_id}/{f.relative_to(kit_dir)}"
                    zf.write(f, arcname)
        # Add soundfonts/
        soundfonts_dir = source_dir / "soundfonts"
        if soundfonts_dir.is_dir():
            for f in soundfonts_dir.iterdir():
                if f.is_file() and f.suffix.lower() == ".sf2":
                    zf.write(f, f"soundfonts/{f.name}")
    return buf.getvalue()


def upload_bundle(client, bucket: str, source_dir: Path, kit_ids: list[str]):
    zip_bytes = build_bundle_zip(source_dir, kit_ids)
    from io import BytesIO
    client.upload_fileobj(
        BytesIO(zip_bytes), bucket, BUNDLE_KEY,
        ExtraArgs={"ContentType": "application/zip"},
    )
    print(f"  Uploaded bundle -> s3://{bucket}/{BUNDLE_KEY}")


def main():
    parser = argparse.ArgumentParser(
        description="Upload drum kits and soundfonts to S3 for on-demand delivery",
    )
    parser.add_argument("source_dir", type=Path, help="Local directory containing drum-kits/ and soundfonts/")
    parser.add_argument("--bucket", "-b", type=str, default=None, help="S3 bucket (or set STORI_AWS_S3_ASSET_BUCKET)")
    parser.add_argument("--region", "-r", type=str, default="us-east-1", help="AWS region")
    parser.add_argument("--no-bundle", action="store_true", help="Do not create all-assets.zip bundle")
    args = parser.parse_args()
    source_dir = args.source_dir.resolve()
    if not source_dir.is_dir():
        print(f"Error: {source_dir} is not a directory", file=sys.stderr)
        sys.exit(1)
    bucket = args.bucket or os.environ.get("STORI_AWS_S3_ASSET_BUCKET")
    if not bucket:
        print("Error: set --bucket or STORI_AWS_S3_ASSET_BUCKET", file=sys.stderr)
        sys.exit(1)
    client = get_s3_client(args.region)
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as e:
        print(f"Error: cannot access bucket {bucket}: {e}", file=sys.stderr)
        sys.exit(1)
    print("Uploading drum kits...")
    drum_kits = upload_drum_kits(client, bucket, source_dir)
    print("Uploading soundfonts...")
    soundfonts = upload_soundfonts(client, bucket, source_dir)
    print("Uploading manifests...")
    upload_manifests(client, bucket, drum_kits, soundfonts)
    if not args.no_bundle and drum_kits and soundfonts:
        print("Uploading bundle (all-assets.zip)...")
        upload_bundle(client, bucket, source_dir, [k["id"] for k in drum_kits])
    print("Done.")


if __name__ == "__main__":
    main()
