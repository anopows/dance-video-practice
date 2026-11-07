from choreo.db import Database
from choreo.retention import extend_available


def test_extension_preserves_alignment_and_never_resurrects_removed_projects(tmp_path):
    db=Database(tmp_path/'room.sqlite3');now=1_000_000
    rows=[('live','ready',now+100),('upload','uploading',now+100),
          ('elapsed','ready',now-1),('expired','expired',now+100),
          ('deleted','deleted',now+100),('failed','failed',now+100),
          ('longer','ready',now+100*86400)]
    with db.connect(write=True) as con:
        for pid,status,expires in rows:
            con.execute("INSERT INTO projects(id,name,bytes,created,expires,upload_deadline,status,updated,revision,alignment) VALUES (?,?,1,?,?,?,?,?,7,?)",
                        (pid,pid,now-100,expires,now+30,status,now,'{"anchor":2}'))
    assert extend_available(db,90*86400,now=now)==2
    with db.connect() as con:
        assert con.execute("SELECT expires FROM projects WHERE id='live'").fetchone()[0]==now+100
    assert extend_available(db,90*86400,apply=True,now=now)==2
    assert extend_available(db,90*86400,apply=True,now=now)==0
    with db.connect() as con:
        for pid,status,old in rows:
            row=con.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
            assert row['expires']==(now-100+90*86400 if pid in ('live','upload') else old)
            assert row['status']==status and row['revision']==7 and row['alignment']=='{"anchor":2}'
