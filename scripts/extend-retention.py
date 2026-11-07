"""Extend existing live projects after configuring the app and bucket retention.

Dry run by default. Deleted, failed or already expired projects stay unavailable.
"""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from choreo.db import Database
from choreo.retention import extend_available

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--state-dir',type=Path,default=Path('/var/lib/choreo'))
parser.add_argument('--days',type=int,default=90)
parser.add_argument('--apply',action='store_true')
args=parser.parse_args()
if not 1<=args.days<=3650:parser.error('days must be between 1 and 3650')
count=extend_available(Database(args.state_dir/'room.sqlite3'),args.days*86400,apply=args.apply)
print(f'{"Extended" if args.apply else "Would extend"} {count} available projects to {args.days} days from upload.')
