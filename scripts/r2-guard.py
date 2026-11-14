#!/usr/bin/env python3
"""Inspect or operate the persistent R2 cost latch on the application host."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from choreo.r2_guard import R2Guard

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('action', choices=['initialize', 'status', 'stop', 'resume'])
parser.add_argument('--state', type=Path, default=Path('/var/lib/choreo/r2-guard.sqlite3'))
parser.add_argument('--reason', default='Manual operator stop')
parser.add_argument('--reviewed', action='store_true', help='Confirm the cause and bill were reviewed before resuming')
args = parser.parse_args()
guard = R2Guard(args.state)
if args.action == 'initialize':
    guard = R2Guard.initialize(args.state)
elif args.action == 'stop':
    guard.stop(args.reason)
elif args.action == 'resume':
    if not args.reviewed:
        parser.error('Resume requires --reviewed after checking the cause and Cloudflare billing.')
    with guard.connect() as db:
        row = db.execute('SELECT * FROM limits WHERE id=1').fetchone()
        if any(row['used_'+k] >= row[k] for k in ('operations', 'upload_bytes', 'media_bytes')):
            parser.error('An allowance is exhausted. Review and explicitly raise that allowance; counters must not be reset.')
        db.execute("UPDATE limits SET stopped='', stopped_at=NULL WHERE id=1")
print(json.dumps(guard.status(), indent=2))
