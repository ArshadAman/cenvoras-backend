#!/usr/bin/env python3
"""
Restore Cenvora PostgreSQL Database from Cloudinary Backup.
Usage:
    python restore_db_backup.py [--list] [--backup-id PUBLIC_ID] [--apply]
"""

import argparse
import datetime
import gzip
import os
import subprocess
import sys
import tempfile
import urllib.request
from dotenv import load_dotenv

# Load environment variables
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    load_dotenv('.env')

try:
    import cloudinary
    import cloudinary.api
    import cloudinary.utils
except ImportError:
    print("Error: 'cloudinary' library not installed. Run: pip install cloudinary")
    sys.exit(1)


def init_cloudinary():
    cloud_name = os.getenv('CLOUDINARY_CLOUD_NAME')
    api_key = os.getenv('CLOUDINARY_API_KEY')
    api_secret = os.getenv('CLOUDINARY_API_SECRET')
    if not cloud_name or not api_key or not api_secret:
        print("Error: Cloudinary credentials missing in .env")
        sys.exit(1)
    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True,
    )


def list_backups():
    init_cloudinary()
    folder = os.getenv('BACKUP_CLOUDINARY_FOLDER', 'cenvoras/db_backups')
    backup_type = os.getenv('BACKUP_CLOUDINARY_TYPE', 'private').strip().lower()
    res = cloudinary.api.resources(type=backup_type, resource_type='raw', prefix=f"{folder}/backup_", max_results=50)
    items = sorted(
        res.get('resources', []),
        key=lambda x: str(x.get('created_at') or ''),
        reverse=True,
    )
    return items


def download_backup(public_id, dest_path):
    init_cloudinary()
    backup_type = os.getenv('BACKUP_CLOUDINARY_TYPE', 'private').strip().lower()
    url = cloudinary.utils.private_download_url(
        public_id,
        'sql.gz',
        resource_type='raw',
        type=backup_type,
    )
    print(f"Downloading backup from Cloudinary: {public_id} ...")
    urllib.request.urlretrieve(url, dest_path)
    size_mb = os.path.getsize(dest_path) / (1024 * 1024)
    print(f"Downloaded {size_mb:.2f} MB to {dest_path}")


def restore_into_postgres(gz_path):
    db_name = os.getenv('POSTGRES_DB', 'cenvoras_db')
    db_user = os.getenv('POSTGRES_USER', 'cenvoras_user')
    
    # Try docker compose first, fallback to psql directly
    cmd = None
    for compose_cmd in [["docker", "compose"], ["docker-compose"]]:
        try:
            res = subprocess.run(compose_cmd + ["version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode == 0:
                cmd = compose_cmd + ["exec", "-T", "db", "psql", "-U", db_user, "-d", db_name]
                break
        except FileNotFoundError:
            continue

    if not cmd:
        cmd = ["psql", "-h", os.getenv('POSTGRES_HOST', 'localhost'), "-p", os.getenv('POSTGRES_PORT', '5432'), "-U", db_user, "-d", db_name]

    print(f"Restoring database with command: {' '.join(cmd)}")
    with gzip.open(gz_path, 'rb') as gz_in:
        proc = subprocess.Popen(cmd, stdin=gz_in, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = proc.communicate()

    if proc.returncode != 0:
        print(f"Restore stderr:\n{stderr.decode('utf-8', errors='replace')}")
        print("Restore completed with some warnings or errors (standard for existing tables/roles).")
    else:
        print("Database restored successfully!")


def main():
    parser = argparse.ArgumentParser(description="Restore Cenvora PostgreSQL Database from Cloudinary")
    parser.add_argument("--list", action="store_true", help="List available backups in Cloudinary")
    parser.add_argument("--backup-id", type=str, help="Specific public_id to restore")
    parser.add_argument("--apply", action="store_true", help="Confirm restoration into PostgreSQL")
    args = parser.parse_args()

    backups = list_backups()
    if not backups:
        print("No backups found in Cloudinary.")
        return

    if args.list:
        print(f"\nFound {len(backups)} backups in Cloudinary:")
        for idx, item in enumerate(backups):
            size_kb = item.get('bytes', 0) / 1024
            created = item.get('created_at')
            pid = item.get('public_id')
            marker = " (LATEST)" if idx == 0 else ""
            print(f"  [{idx + 1}] {pid} ({size_kb:.1f} KB, {created}){marker}")
        return

    target_id = args.backup_id
    if not target_id:
        target_id = backups[0].get('public_id')
        print(f"Selected latest backup: {target_id}")

    if not args.apply:
        print(f"\nDry run complete. To restore '{target_id}' into PostgreSQL, run:")
        print(f"  python restore_db_backup.py --backup-id \"{target_id}\" --apply")
        return

    with tempfile.NamedTemporaryFile(suffix=".sql.gz", delete=False) as tmp_file:
        tmp_path = tmp_file.name

    try:
        download_backup(target_id, tmp_path)
        restore_into_postgres(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


if __name__ == '__main__':
    main()
