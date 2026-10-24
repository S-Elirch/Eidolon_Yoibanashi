"""Async persistence using the host's single Database writer queue."""
import json
import secrets
from .policy import probability as chance_probability

def _prepare(db):
    db.execute('CREATE TABLE IF NOT EXISTS dev2_night_tasks (user TEXT, character TEXT, night TEXT, conversation TEXT, state TEXT, snapshot TEXT, PRIMARY KEY(user,character,night))')
    db.execute('CREATE TABLE IF NOT EXISTS dev2_night_opportunities (user TEXT, character TEXT, night TEXT, tier TEXT, probability REAL, draw REAL, eligible INTEGER, PRIMARY KEY(user,character,night))')
    db.execute("UPDATE dev2_night_tasks SET state='interrupted' WHERE state IN ('preparing','ready','typing','streaming')")

def _opportunity(db, c, night, tier):
    # Disabled affinity keeps mock/original behaviour; enabled tiers use the frozen draw.
    probability = chance_probability(tier)
    if not db.execute('SELECT 1 FROM users WHERE id=?', (c.user_id,)).fetchone():
        return dict(tier=tier, probability=probability, draw=None, eligible=False)
    row = db.execute('SELECT tier,probability,draw,eligible FROM dev2_night_opportunities WHERE user=? AND character=? AND night=?',
                     (c.user_id,c.character_id,night)).fetchone()
    if row is None:
        draw = secrets.randbelow(1_000_000) / 1_000_000
        row = (tier, probability, draw, int(draw < probability))
        db.execute('INSERT INTO dev2_night_opportunities VALUES (?,?,?,?,?,?,?)',
                   (c.user_id,c.character_id,night,*row))
    return dict(tier=row[0], probability=row[1], draw=row[2], eligible=bool(row[3]))

def _opportunity_history(db, user):
    rows = db.execute('SELECT character,night,tier,probability,draw,eligible FROM dev2_night_opportunities WHERE user=? ORDER BY night DESC,character', (user,)).fetchall()
    return [dict(character=r[0],night=r[1],tier=r[2],probability=r[3],draw=r[4],eligible=bool(r[5])) for r in rows]

def _write(db, job):
    # Serialize the existence check with Store deletion: a cancelled task's finally
    # must never recreate a deleted user's snapshot.
    if not db.execute('SELECT 1 FROM conversations c JOIN users u ON u.id=c.user_id WHERE c.id=? AND u.id=?',
                      (job['conversation'],job['key'][0])).fetchone():
        return False
    db.execute('INSERT OR REPLACE INTO dev2_night_tasks VALUES (?,?,?,?,?,?)',
        (*job['key'], job['conversation'], job['state'], json.dumps({k:v for k,v in job.items() if k not in ('task','subscribers','key')}, ensure_ascii=False)))
    return True

def _find(db, key):
    row = db.execute('SELECT state,snapshot FROM dev2_night_tasks WHERE user=? AND character=? AND night=?', key).fetchone()
    if row:
        job = json.loads(row[1]) | {'key': tuple(key), 'state': row[0], 'subscribers': 0}
        return job

def _status(db, user):
    rows = db.execute('SELECT character,night,state,snapshot FROM dev2_night_tasks WHERE user=?', (user,)).fetchall()
    return [dict(character=r[0],night=r[1].split('#invite:',1)[0],invitation='#invite:' in r[1],state=r[2],calls=json.loads(r[3]).get('calls',0),successes=json.loads(r[3]).get('successes',0),opening=json.loads(r[3]).get('opening'),reason=json.loads(r[3]).get('reason')) for r in rows]

def _recent_openings(db, c):
    # Only completed visits count; silence/failure must not force a rotation.
    rows = db.execute("SELECT snapshot FROM dev2_night_tasks WHERE user=? AND character=? AND state='done' ORDER BY json_extract(snapshot,'$.created') DESC LIMIT 2",
                      (c.user_id,c.character_id)).fetchall()
    return tuple(json.loads(row[0]).get('opening', 'unknown') for row in rows)

def _prune(db, cutoff):
    db.execute("DELETE FROM dev2_night_tasks WHERE state NOT IN ('preparing','ready','typing','streaming') AND json_extract(snapshot,'$.created') < ?", (cutoff,))

def _attempts(db, user, night):
    rows = db.execute('SELECT snapshot FROM dev2_night_tasks WHERE user=? AND night=?', (user,night)).fetchall()
    return sum(json.loads(row[0]).get('calls', 0) for row in rows)

def _reserve(db, job):
    previous = _find(db, job['key'])
    if previous is not None:
        return 'existing', previous
    user, character, period = job['key']
    if '#invite:' in period:
        night = period.split('#invite:', 1)[0]
        if not db.execute('SELECT 1 FROM visit_outreach_on WHERE user_id=? AND tonight=?', (user, night)).fetchone():
            return 'invitation_ended', None
        # Avoid overlapping preparations across characters; sequential visits remain allowed.
        if db.execute("SELECT 1 FROM dev2_night_tasks WHERE user=? AND state IN ('preparing','ready','typing','streaming')", (user,)).fetchone():
            return 'invitation_claimed', None
    elif _attempts(db, user, period) >= 3:
        return 'capped', None
    return ('created' if _write(db, job) else 'deleted'), None

def _snapshot(job):
    return json.loads(json.dumps({k:v for k,v in job.items() if k not in ('task','subscribers')}, ensure_ascii=False))

class Repository:
    def __init__(self, database):
        self.database = database
        database.prepare(_prepare)

    async def write(self, job):
        return await self.database.write(_write, _snapshot(job))

    async def reserve(self, job):
        return await self.database.write(_reserve, _snapshot(job))

    async def opportunity(self, c, night, tier):
        return await self.database.write(_opportunity, c, night, tier)

    async def opportunity_history(self, user):
        return await self.database.read(_opportunity_history, user)

    async def find(self, key):
        return await self.database.read(_find, key)

    async def status(self, user):
        return await self.database.read(_status, user)

    async def recent_openings(self, c):
        return await self.database.read(_recent_openings, c)

    async def prune(self, cutoff):
        return await self.database.write(_prune, cutoff)

    async def attempts(self, user, night):
        return await self.database.read(_attempts, user, night)
