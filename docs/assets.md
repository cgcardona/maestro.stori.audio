# Assets (drum kits & soundfonts)

- **Drum kits:** `assets/drum-kits/` — one folder per kit with `kit.json`. Zipped and uploaded to S3 for on-demand download.
- **Soundfonts:** One GM `.sf2` file (e.g. **FluidR3_GM.sf2**) in `assets/soundfonts/` for melodic instruments. Download from [FluidSynth](https://github.com/FluidSynth/fluidsynth/wiki/SoundFont); ~140 MB (do not commit; add `*.sf2` to `.gitignore`).

**Upload to S3** (from repo root, bucket/region in `.env` or CLI):

```bash
python scripts/upload_assets_to_s3.py ../../assets --bucket YOUR_BUCKET --region YOUR_REGION
```

API serves presigned download URLs (see [integrate.md](integrate.md)). S3 setup: [setup.md](setup.md#deploy-day-to-day).
