#!/usr/bin/env python3
"""Local emergency moderation. Uses the same transactions and mail as the API."""
import argparse,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from choreo.config import settings
from choreo.db import Database
from choreo.legal import LegalPages
from choreo.mail import Mailer
from choreo.moderation import Moderation
from choreo.accounts import normalize_email
p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
for action in ('pause-uploads','resume-uploads','hold','remove','quarantine','ban','unban','release-hold'):
    parser=sub.add_parser(action)
    if action not in ('pause-uploads','resume-uploads'):parser.add_argument('target')
    parser.add_argument('--reason',required=action in ('pause-uploads','hold','remove','quarantine','ban'),default='Operator reviewed')
q=sub.add_parser('release-quarantine');q.add_argument('target');g=q.add_mutually_exclusive_group(required=True);g.add_argument('--delete',action='store_true');g.add_argument('--restore',action='store_true')
a=p.parse_args();cfg=settings();db=Database(cfg.state_dir/'room.sqlite3');db.migrate(cfg);m=Moderation(cfg,db,Mailer(cfg,db),LegalPages(cfg.legal_dir))
if a.command in ('pause-uploads','resume-uploads'):m.flags(a.command=='pause-uploads',a.reason,'cli')
elif a.command=='release-quarantine':m.release_quarantine(a.target,a.restore)
elif a.command in ('hold','remove','quarantine'):m.project_action(a.target,a.command,a.reason,'cli')
else:
    _,key=normalize_email(a.target)
    with db.connect() as con:row=con.execute('SELECT id FROM users WHERE email_key=?',(key,)).fetchone()
    if not row:p.error('Account not found')
    m.user_action(row['id'],a.command.replace('-','_'),a.reason,'cli')
print('Action recorded.')
