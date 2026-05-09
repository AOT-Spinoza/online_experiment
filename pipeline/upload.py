#!/usr/bin/env python3
"""
Upload pipeline/staging_hashed/ to a Cloudflare R2 bucket (or any
S3-compatible object store).

Reads credentials from .env at the repo root — see .env.example for the
schema. The required variables are:

    BUCKET_ENDPOINT          e.g. https://<account-id>.r2.cloudflarestorage.com
    BUCKET_ACCESS_KEY_ID
    BUCKET_SECRET_ACCESS_KEY
    BUCKET_NAME              the bucket to upload into
    BUCKET_REGION            'auto' for R2, otherwise the AWS region
    PUBLIC_BASE_URL          (optional, only for the post-run reminder)

Behaviour:
- **Idempotent**: HEAD-checks each object first and skips if already
  present at the same size. Re-runs only push new or changed files.
- **Parallel**: ThreadPoolExecutor with a configurable worker count.
  R2 handles concurrent PUTs cheerfully.
- **Correct headers**: each object lands with `Content-Type: video/mp4`
  (so the r2.dev public URL serves it correctly to <video> elements)
  and `Cache-Control: public, max-age=31536000, immutable` (videos
  have hashed filenames so they're effectively immutable — caching
  hard means a participant who refreshes mid-experiment doesn't
  re-download).

Usage:

    python pipeline/upload.py                 # full run
    python pipeline/upload.py --dry-run       # what would happen
    python pipeline/upload.py --workers 16    # speed it up
    python pipeline/upload.py --prefix vids   # nest under a prefix
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
    from dotenv import load_dotenv
    from tqdm import tqdm
except ImportError as e:
    print(
        f"error: missing Python dependency ({e.name}). install with:\n"
        f"    python -m pip install -r pipeline/requirements.txt",
        file=sys.stderr,
    )
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parent.parent


def make_client(endpoint: str, access_key: str, secret_key: str, region: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region or "auto",
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 5, "mode": "standard"},
            max_pool_connections=64,  # well above worker count
        ),
    )


def needs_upload(client, bucket: str, key: str, local_size: int) -> bool:
    try:
        head = client.head_object(Bucket=bucket, Key=key)
        return int(head["ContentLength"]) != local_size
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("404", "NoSuchKey", "NotFound") or status == 404:
            return True
        raise


def upload_one(client, bucket: str, key: str, local_path: Path) -> tuple[str, str]:
    try:
        size = local_path.stat().st_size
        if not needs_upload(client, bucket, key, size):
            return (key, "skip")
        client.upload_file(
            Filename=str(local_path),
            Bucket=bucket,
            Key=key,
            ExtraArgs={
                "ContentType": "video/mp4",
                "CacheControl": "public, max-age=31536000, immutable",
            },
        )
        return (key, "uploaded")
    except Exception as e:  # noqa: BLE001 — we report; main() decides exit
        return (key, f"fail: {type(e).__name__}: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source", type=Path, default=REPO_ROOT / "pipeline" / "staging_hashed",
        help="Directory of hashed-name MP4 files to upload. (default: %(default)s)",
    )
    parser.add_argument(
        "--prefix", default="",
        help="Key prefix in the bucket (default: bucket root). Trailing slash optional.",
    )
    parser.add_argument(
        "--workers", type=int, default=8,
        help="Parallel upload threads. R2 handles plenty more if you need throughput.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Cap the number of files (for partial uploads / pilot runs).",
    )
    parser.add_argument("--dry-run", action="store_true",
        help="Don't actually upload; just print what would happen.")
    args = parser.parse_args()

    # Load .env from repo root (so this script works no matter where it's invoked from).
    load_dotenv(REPO_ROOT / ".env")
    endpoint = os.environ.get("BUCKET_ENDPOINT")
    access_key = os.environ.get("BUCKET_ACCESS_KEY_ID")
    secret_key = os.environ.get("BUCKET_SECRET_ACCESS_KEY")
    region = os.environ.get("BUCKET_REGION", "auto")
    bucket = os.environ.get("BUCKET_NAME")
    public_base = os.environ.get("PUBLIC_BASE_URL", "")

    missing = [
        name for name, val in [
            ("BUCKET_ENDPOINT", endpoint),
            ("BUCKET_ACCESS_KEY_ID", access_key),
            ("BUCKET_SECRET_ACCESS_KEY", secret_key),
            ("BUCKET_NAME", bucket),
        ]
        if not val
    ]
    if missing:
        print(
            f"error: missing required env vars: {', '.join(missing)}\n"
            f"copy .env.example to .env and fill them in.",
            file=sys.stderr,
        )
        return 2

    if not args.source.exists():
        print(f"error: source dir not found: {args.source}", file=sys.stderr)
        print("       have you run pipeline/transcode.py yet?", file=sys.stderr)
        return 2

    files = sorted(args.source.glob("*.mp4"))
    if args.limit:
        files = files[: args.limit]
    if not files:
        print(f"error: no .mp4 files in {args.source}", file=sys.stderr)
        return 2

    total_bytes = sum(f.stat().st_size for f in files)
    print(f"source:        {args.source.relative_to(REPO_ROOT)}")
    print(f"bucket:        {bucket}")
    print(f"endpoint:      {endpoint}")
    print(f"files:         {len(files):,}  ({total_bytes / 1_000_000:.1f} MB total)")
    print(f"workers:       {args.workers}")
    if args.prefix:
        print(f"key prefix:    {args.prefix.rstrip('/') + '/'}")
    print()

    if args.dry_run:
        print("dry run — first 5 files that would be considered:")
        for f in files[:5]:
            print(f"  {f.name}  ({f.stat().st_size:,} bytes)")
        if len(files) > 5:
            print(f"  ... and {len(files) - 5:,} more")
        return 0

    client = make_client(endpoint, access_key, secret_key, region)

    # Pre-flight: try to HEAD the bucket itself so a misconfigured token /
    # endpoint fails loudly before we start hammering it with uploads.
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as e:
        print(f"error: head_bucket({bucket!r}) failed — {e}", file=sys.stderr)
        print(
            "       check BUCKET_NAME and that the API token has access to it.",
            file=sys.stderr,
        )
        return 2

    def make_key(f: Path) -> str:
        return (args.prefix.rstrip("/") + "/" + f.name).lstrip("/") if args.prefix else f.name

    counts = {"uploaded": 0, "skip": 0, "fail": 0}
    failures: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(upload_one, client, bucket, make_key(f), f) for f in files]
        for fut in tqdm(as_completed(futures), total=len(futures), unit="file"):
            key, result = fut.result()
            if result == "uploaded":
                counts["uploaded"] += 1
            elif result == "skip":
                counts["skip"] += 1
            else:
                counts["fail"] += 1
                failures.append((key, result))

    print()
    print(f"  uploaded: {counts['uploaded']:>5d}")
    print(f"  skipped:  {counts['skip']:>5d}  (already present at same size)")
    if counts["fail"]:
        print(f"  failed:   {counts['fail']:>5d}", file=sys.stderr)
        for key, reason in failures[:10]:
            print(f"    {key}: {reason}", file=sys.stderr)
        if len(failures) > 10:
            print(f"    ... and {len(failures) - 10} more", file=sys.stderr)
        return 1

    if public_base:
        sample_key = make_key(files[0])
        print()
        print(f"sanity check — try this URL in a browser:")
        print(f"  {public_base.rstrip('/')}/{sample_key}")
        print(f"if it serves a video, you're done. then:")
        print(f"  python pipeline/build_manifest.py --base-url {public_base.rstrip('/')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
