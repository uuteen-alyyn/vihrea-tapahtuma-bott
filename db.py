"""
SQLite layer: schema, guild config, audit log, rate limiting, taxonomy cache,
and pending email submissions.
"""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from cryptography.fernet import Fernet

# Defaults — overwritten by init_db()
_db_path = "audit.db"
_fernet: Fernet = None

# ---------------------------------------------------------------------------
# Default taxonomy values (no API endpoint exists; admin can update via /taxonomy)
# ---------------------------------------------------------------------------
DEFAULT_MUNICIPALITIES = [
    # Uusimaa
    "Askola", "Espoo", "Hanko", "Helsinki", "Hyvinkää", "Inkoo",
    "Järvenpää", "Karkkila", "Kauniainen", "Kerava", "Kirkkonummi",
    "Lapinjärvi", "Lohja", "Loviisa", "Myrskylä", "Mäntsälä",
    "Nurmijärvi", "Pornainen", "Porvoo", "Pukkila", "Raasepori",
    "Sipoo", "Siuntio", "Tuusula", "Vantaa", "Vihti",
    # Varsinais-Suomi
    "Aura", "Kaarina", "Kemiönsaari", "Koski Tl", "Kustavi", "Laitila",
    "Lieto", "Loimaa", "Länsi-Turunmaa", "Marttila", "Masku", "Mynämäki",
    "Naantali", "Nousiainen", "Oripää", "Paimio", "Parainen", "Pyhäranta",
    "Pöytyä", "Raisio", "Rusko", "Salo", "Sauvo", "Somero", "Taivassalo",
    "Tarvasjoki", "Turku", "Uusikaupunki",
    # Pirkanmaa
    "Akaa", "Hämeenkyrö", "Ikaalinen", "Juupajoki", "Kangasala",
    "Kihniö", "Lempäälä", "Mänttä-Vilppula", "Nokia", "Orivesi",
    "Parkano", "Pirkkala", "Punkalaidun", "Pälkäne", "Ruovesi",
    "Sastamala", "Tampere", "Urjala", "Valkeakoski", "Vesilahti",
    "Virrat", "Ylöjärvi",
    # Pohjois-Pohjanmaa
    "Haapajärvi", "Haapavesi", "Hailuoto", "Ii", "Kalajoki", "Kempele",
    "Kuusamo", "Kärsämäki", "Liminka", "Lumijoki", "Merijärvi", "Muhos",
    "Nivala", "Oulainen", "Oulu", "Pudasjärvi", "Pyhäjoki", "Pyhäjärvi",
    "Pyhäntä", "Raahe", "Reisjärvi", "Sievi", "Siikajoki", "Siikalatva",
    "Taivalkoski", "Tyrnävä", "Utajärvi", "Vaala", "Ylivieska",
    # Keski-Suomi
    "Hankasalmi", "Joutsa", "Jyväskylä", "Jämsä", "Kannonkoski",
    "Karstula", "Keuruu", "Kinnula", "Kivijärvi", "Konnevesi",
    "Kyyjärvi", "Laukaa", "Luhanka", "Multia", "Muurame", "Petäjävesi",
    "Pihtipudas", "Saarijärvi", "Toivakka", "Uurainen", "Viitasaari",
    # Pohjois-Savo
    "Iisalmi", "Juankoski", "Kaavi", "Karttula", "Keitele", "Kiuruvesi",
    "Kuopio", "Lapinlahti", "Leppävirta", "Maaninka", "Nilsiä",
    "Pielavesi", "Rautalampi", "Rautavaara", "Siilinjärvi", "Sonkajärvi",
    "Suonenjoki", "Tervo", "Tuusniemi", "Varkaus", "Vesanto", "Vieremä",
    # Pohjois-Karjala
    "Ilomantsi", "Joensuu", "Juuka", "Kitee", "Kontiolahti", "Lieksa",
    "Liperi", "Nurmes", "Outokumpu", "Polvijärvi", "Rääkkylä",
    "Tohmajärvi", "Valtimo",
    # Etelä-Suomi / muut suuret
    "Lahti", "Hämeenlinna", "Kouvola", "Kotka", "Lappeenranta",
    "Imatra", "Mikkeli", "Savonlinna", "Joensuu", "Kuopio", "Pori",
    "Rauma", "Vaasa", "Seinäjoki", "Kokkola", "Rovaniemi", "Kemi",
    "Tornio", "Kajaani", "Joensuu",
]
DEFAULT_EVENT_TYPES = [
    "Juhla", "Keskustelutilaisuus", "Kokous",
    "Koulutus", "Seminaari", "Toritapahtuma",
]


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init_db(path: str, encryption_key: bytes) -> None:
    global _db_path, _fernet
    _db_path = path
    _fernet = Fernet(encryption_key)
    _create_tables()
    _seed_taxonomy()


@contextmanager
def _conn():
    con = sqlite3.connect(_db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _create_tables() -> None:
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id        INTEGER PRIMARY KEY,
                submission_channel_id INTEGER,
                admin_role_id   INTEGER,
                default_organiser TEXT DEFAULT '',
                api_key_encrypted TEXT DEFAULT '',
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT DEFAULT (datetime('now')),
                channel         TEXT NOT NULL,
                guild_id        INTEGER,
                user_id         TEXT,
                action          TEXT NOT NULL,
                details         TEXT,
                submission_id   TEXT
            );

            CREATE TABLE IF NOT EXISTS pending_email_submissions (
                id              TEXT PRIMARY KEY,
                email_from      TEXT NOT NULL,
                email_subject   TEXT,
                received_at     TEXT NOT NULL,
                expires_at      TEXT NOT NULL,
                event_data      TEXT NOT NULL,
                status          TEXT DEFAULT 'pending',
                reply_count     INTEGER DEFAULT 0,
                guild_id        INTEGER
            );

            CREATE TABLE IF NOT EXISTS rate_limit_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         TEXT NOT NULL,
                guild_id        INTEGER NOT NULL,
                submitted_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS taxonomy_cache (
                term_type       TEXT NOT NULL,
                term_value      TEXT NOT NULL,
                PRIMARY KEY (term_type, term_value)
            );
        """)


def _seed_taxonomy() -> None:
    """Replace municipality and event_type defaults. Never touches organiser entries."""
    with _conn() as con:
        con.execute("DELETE FROM taxonomy_cache WHERE term_type = 'municipality'")
        con.execute("DELETE FROM taxonomy_cache WHERE term_type = 'event_type'")
        # Deduplicate before inserting
        seen: set = set()
        rows = []
        for m in DEFAULT_MUNICIPALITIES:
            if m not in seen:
                seen.add(m)
                rows.append(("municipality", m))
        for et in DEFAULT_EVENT_TYPES:
            rows.append(("event_type", et))
        con.executemany(
            "INSERT OR IGNORE INTO taxonomy_cache (term_type, term_value) VALUES (?, ?)",
            rows,
        )


# ---------------------------------------------------------------------------
# Guild config
# ---------------------------------------------------------------------------

def get_guild_config(guild_id: int) -> Optional[dict]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        return dict(row) if row else None


def upsert_guild_config(
    guild_id: int,
    submission_channel_id: Optional[int] = None,
    admin_role_id: Optional[int] = None,
    default_organiser: Optional[str] = None,
) -> None:
    with _conn() as con:
        existing = con.execute(
            "SELECT guild_id FROM guild_config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        if existing:
            updates = []
            params = []
            if submission_channel_id is not None:
                updates.append("submission_channel_id = ?")
                params.append(submission_channel_id)
            if admin_role_id is not None:
                updates.append("admin_role_id = ?")
                params.append(admin_role_id)
            if default_organiser is not None:
                updates.append("default_organiser = ?")
                params.append(default_organiser)
            updates.append("updated_at = datetime('now')")
            params.append(guild_id)
            con.execute(
                f"UPDATE guild_config SET {', '.join(updates)} WHERE guild_id = ?",
                params,
            )
        else:
            con.execute(
                """INSERT INTO guild_config
                   (guild_id, submission_channel_id, admin_role_id, default_organiser)
                   VALUES (?, ?, ?, ?)""",
                (guild_id, submission_channel_id, admin_role_id, default_organiser or ""),
            )


def set_api_key(guild_id: int, api_key: str) -> None:
    encrypted = _fernet.encrypt(api_key.encode()).decode()
    with _conn() as con:
        con.execute(
            """INSERT INTO guild_config (guild_id, api_key_encrypted)
               VALUES (?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET
                   api_key_encrypted = excluded.api_key_encrypted,
                   updated_at = datetime('now')""",
            (guild_id, encrypted),
        )


def get_api_key(guild_id: int) -> Optional[str]:
    with _conn() as con:
        row = con.execute(
            "SELECT api_key_encrypted FROM guild_config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        if not row or not row["api_key_encrypted"]:
            return None
        return _fernet.decrypt(row["api_key_encrypted"].encode()).decode()


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def audit(
    channel: str,
    action: str,
    guild_id: Optional[int] = None,
    user_id: Optional[str] = None,
    details: Optional[dict] = None,
    submission_id: Optional[str] = None,
) -> None:
    with _conn() as con:
        con.execute(
            """INSERT INTO audit_log (channel, guild_id, user_id, action, details, submission_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                channel,
                guild_id,
                user_id,
                action,
                json.dumps(details, ensure_ascii=False) if details else None,
                submission_id,
            ),
        )


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def check_rate_limit(user_id: str, guild_id: int, max_count: int, window_seconds: int) -> bool:
    """Return True if the user is within the rate limit (allowed to submit)."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    ).strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as con:
        count = con.execute(
            """SELECT COUNT(*) FROM rate_limit_log
               WHERE user_id = ? AND guild_id = ? AND submitted_at > ?""",
            (user_id, guild_id, cutoff),
        ).fetchone()[0]
    return count < max_count


def record_submission(user_id: str, guild_id: int) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO rate_limit_log (user_id, guild_id, submitted_at) VALUES (?, ?, datetime('now'))",
            (user_id, guild_id),
        )


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

def get_taxonomy(term_type: str) -> list[str]:
    with _conn() as con:
        rows = con.execute(
            "SELECT term_value FROM taxonomy_cache WHERE term_type = ? ORDER BY term_value",
            (term_type,),
        ).fetchall()
        return [r["term_value"] for r in rows]


def add_taxonomy_term(term_type: str, term_value: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO taxonomy_cache (term_type, term_value) VALUES (?, ?)",
            (term_type, term_value),
        )


def remove_taxonomy_term(term_type: str, term_value: str) -> None:
    with _conn() as con:
        con.execute(
            "DELETE FROM taxonomy_cache WHERE term_type = ? AND term_value = ?",
            (term_type, term_value),
        )


# ---------------------------------------------------------------------------
# Pending email submissions
# ---------------------------------------------------------------------------

def create_pending_email(
    email_from: str,
    email_subject: str,
    event_data: dict,
    confirmation_window_seconds: int = 3600,
    guild_id: Optional[int] = None,
) -> str:
    sub_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=confirmation_window_seconds)
    with _conn() as con:
        con.execute(
            """INSERT INTO pending_email_submissions
               (id, email_from, email_subject, received_at, expires_at, event_data, guild_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                sub_id,
                email_from,
                email_subject,
                now.strftime("%Y-%m-%d %H:%M:%S"),
                expires.strftime("%Y-%m-%d %H:%M:%S"),
                json.dumps(event_data, ensure_ascii=False),
                guild_id,
            ),
        )
    return sub_id


def get_pending_by_email(email_from: str) -> Optional[dict]:
    """Return the most recent pending submission for an email address."""
    with _conn() as con:
        row = con.execute(
            """SELECT * FROM pending_email_submissions
               WHERE email_from = ? AND status = 'pending'
               ORDER BY received_at DESC LIMIT 1""",
            (email_from,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["event_data"] = json.loads(d["event_data"])
        return d


def update_pending_email(sub_id: str, event_data: dict, reply_count: int) -> None:
    with _conn() as con:
        con.execute(
            """UPDATE pending_email_submissions
               SET event_data = ?, reply_count = ?
               WHERE id = ?""",
            (json.dumps(event_data, ensure_ascii=False), reply_count, sub_id),
        )


def close_pending_email(sub_id: str, status: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE pending_email_submissions SET status = ? WHERE id = ?",
            (status, sub_id),
        )


def get_expired_pending_emails() -> list[dict]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as con:
        rows = con.execute(
            """SELECT * FROM pending_email_submissions
               WHERE status = 'pending' AND expires_at <= ?""",
            (now,),
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["event_data"] = json.loads(d["event_data"])
            result.append(d)
        return result
