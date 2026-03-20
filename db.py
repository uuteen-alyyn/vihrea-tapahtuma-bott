"""
SQLAlchemy layer: schema, guild config, audit log, rate limiting, taxonomy cache,
and pending email submissions.
"""
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

class GuildConfig(Base):
    __tablename__ = 'guild_config'
    guild_id = Column(BigInteger, primary_key=True)
    submission_channel_id = Column(BigInteger, nullable=True)
    admin_role_id = Column(BigInteger, nullable=True)
    default_organiser = Column(String(255), default='')
    api_key_encrypted = Column(Text, default='')
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class AuditLog(Base):
    __tablename__ = 'audit_log'
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    channel = Column(String(255), nullable=False)
    guild_id = Column(BigInteger, nullable=True)
    user_id = Column(String(255), nullable=True)
    action = Column(String(255), nullable=False)
    details = Column(Text, nullable=True)
    submission_id = Column(String(255), nullable=True)

class PendingEmailSubmission(Base):
    __tablename__ = 'pending_email_submissions'
    id = Column(String(255), primary_key=True)
    email_from = Column(String(255), nullable=False)
    email_subject = Column(String(255), nullable=True)
    received_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    event_data = Column(Text, nullable=False)
    status = Column(String(50), default='pending')
    reply_count = Column(Integer, default=0)
    guild_id = Column(BigInteger, nullable=True)

class RateLimitLog(Base):
    __tablename__ = 'rate_limit_log'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(255), nullable=False)
    guild_id = Column(BigInteger, nullable=False)
    submitted_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

class TaxonomyCache(Base):
    __tablename__ = 'taxonomy_cache'
    term_type = Column(String(255), primary_key=True)
    term_value = Column(String(255), primary_key=True)

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
    "Tornio", "Kajaani"
]

DEFAULT_EVENT_TYPES = [
    "Juhla", "Keskustelutilaisuus", "Kokous",
    "Koulutus", "Seminaari", "Toritapahtuma",
]


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

_engine = None
_SessionFactory = None
_fernet: Fernet = None

def init_db(database_url: str, encryption_key: bytes) -> None:
    global _engine, _SessionFactory, _fernet
    if database_url.startswith("sqlite"):
        _engine = create_engine(database_url, connect_args={"check_same_thread": False})
    else:
        _engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=3600)

    _SessionFactory = sessionmaker(bind=_engine)
    _fernet = Fernet(encryption_key)
    
    Base.metadata.create_all(_engine)
    _seed_taxonomy()


@contextmanager
def get_session():
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _seed_taxonomy() -> None:
    """Replace municipality and event_type defaults. Never touches organiser entries."""
    with get_session() as session:
        session.query(TaxonomyCache).filter(TaxonomyCache.term_type.in_(['municipality', 'event_type'])).delete(synchronize_session=False)
        
        # Deduplicate before inserting
        seen = set()
        objects = []
        for m in DEFAULT_MUNICIPALITIES:
            if m not in seen:
                seen.add(m)
                objects.append(TaxonomyCache(term_type='municipality', term_value=m))
        for et in DEFAULT_EVENT_TYPES:
            objects.append(TaxonomyCache(term_type='event_type', term_value=et))
        
        # Merge objects to avoid integrity errors on duplicate inserts if they somehow exist
        for obj in objects:
            session.merge(obj)


# ---------------------------------------------------------------------------
# Guild config
# ---------------------------------------------------------------------------

def get_guild_config(guild_id: int) -> Optional[dict]:
    with get_session() as session:
        record = session.query(GuildConfig).filter_by(guild_id=guild_id).first()
        if not record:
            return None
        return {
            "guild_id": record.guild_id,
            "submission_channel_id": record.submission_channel_id,
            "admin_role_id": record.admin_role_id,
            "default_organiser": record.default_organiser,
            "api_key_encrypted": record.api_key_encrypted,
        }

def upsert_guild_config(
    guild_id: int,
    submission_channel_id: Optional[int] = None,
    admin_role_id: Optional[int] = None,
    default_organiser: Optional[str] = None,
) -> None:
    with get_session() as session:
        record = session.query(GuildConfig).filter_by(guild_id=guild_id).first()
        if not record:
            record = GuildConfig(
                guild_id=guild_id,
                submission_channel_id=submission_channel_id,
                admin_role_id=admin_role_id,
                default_organiser=default_organiser or ''
            )
            session.add(record)
        else:
            if submission_channel_id is not None:
                record.submission_channel_id = submission_channel_id
            if admin_role_id is not None:
                record.admin_role_id = admin_role_id
            if default_organiser is not None:
                record.default_organiser = default_organiser

def set_api_key(guild_id: int, api_key: str) -> None:
    encrypted = _fernet.encrypt(api_key.encode()).decode()
    with get_session() as session:
        record = session.query(GuildConfig).filter_by(guild_id=guild_id).first()
        if not record:
            record = GuildConfig(guild_id=guild_id, api_key_encrypted=encrypted)
            session.add(record)
        else:
            record.api_key_encrypted = encrypted

def get_api_key(guild_id: int) -> Optional[str]:
    with get_session() as session:
        record = session.query(GuildConfig).filter_by(guild_id=guild_id).first()
        if not record or not record.api_key_encrypted:
            return None
        return _fernet.decrypt(record.api_key_encrypted.encode()).decode()


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
    with get_session() as session:
        log = AuditLog(
            channel=channel,
            guild_id=guild_id,
            user_id=user_id,
            action=action,
            details=json.dumps(details, ensure_ascii=False) if details else None,
            submission_id=submission_id,
        )
        session.add(log)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def check_rate_limit(user_id: str, guild_id: int, max_count: int, window_seconds: int) -> bool:
    """Return True if the user is within the rate limit (allowed to submit)."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    with get_session() as session:
        count = session.query(RateLimitLog).filter(
            RateLimitLog.user_id == user_id,
            RateLimitLog.guild_id == guild_id,
            RateLimitLog.submitted_at > cutoff
        ).count()
    return count < max_count

def record_submission(user_id: str, guild_id: int) -> None:
    with get_session() as session:
        log = RateLimitLog(user_id=user_id, guild_id=guild_id)
        session.add(log)


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

def get_taxonomy(term_type: str) -> list[str]:
    with get_session() as session:
        records = session.query(TaxonomyCache).filter_by(term_type=term_type).order_by(TaxonomyCache.term_value).all()
        return [r.term_value for r in records]

def add_taxonomy_term(term_type: str, term_value: str) -> None:
    with get_session() as session:
        record = session.query(TaxonomyCache).filter_by(term_type=term_type, term_value=term_value).first()
        if not record:
            session.add(TaxonomyCache(term_type=term_type, term_value=term_value))

def remove_taxonomy_term(term_type: str, term_value: str) -> None:
    with get_session() as session:
        session.query(TaxonomyCache).filter_by(term_type=term_type, term_value=term_value).delete()


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
    with get_session() as session:
        pending = PendingEmailSubmission(
            id=sub_id,
            email_from=email_from,
            email_subject=email_subject,
            received_at=now,
            expires_at=expires,
            event_data=json.dumps(event_data, ensure_ascii=False),
            guild_id=guild_id,
        )
        session.add(pending)
    return sub_id

def get_pending_by_email(email_from: str) -> Optional[dict]:
    """Return the most recent pending submission for an email address."""
    with get_session() as session:
        record = session.query(PendingEmailSubmission).filter_by(
            email_from=email_from, status='pending'
        ).order_by(PendingEmailSubmission.received_at.desc()).first()
        
        if not record:
            return None
            
        return {
            "id": record.id,
            "email_from": record.email_from,
            "email_subject": record.email_subject,
            "received_at": record.received_at.strftime("%Y-%m-%d %H:%M:%S"),
            "expires_at": record.expires_at.strftime("%Y-%m-%d %H:%M:%S"),
            "event_data": json.loads(record.event_data),
            "status": record.status,
            "reply_count": record.reply_count,
            "guild_id": record.guild_id,
        }

def update_pending_email(sub_id: str, event_data: dict, reply_count: int) -> None:
    with get_session() as session:
        record = session.query(PendingEmailSubmission).filter_by(id=sub_id).first()
        if record:
            record.event_data = json.dumps(event_data, ensure_ascii=False)
            record.reply_count = reply_count

def close_pending_email(sub_id: str, status: str) -> None:
    with get_session() as session:
        record = session.query(PendingEmailSubmission).filter_by(id=sub_id).first()
        if record:
            record.status = status

def get_expired_pending_emails() -> list[dict]:
    now = datetime.now(timezone.utc)
    with get_session() as session:
        records = session.query(PendingEmailSubmission).filter(
            PendingEmailSubmission.status == 'pending',
            PendingEmailSubmission.expires_at <= now
        ).all()
        
        result = []
        for record in records:
            result.append({
                "id": record.id,
                "email_from": record.email_from,
                "email_subject": record.email_subject,
                "received_at": record.received_at.strftime("%Y-%m-%d %H:%M:%S"),
                "expires_at": record.expires_at.strftime("%Y-%m-%d %H:%M:%S"),
                "event_data": json.loads(record.event_data),
                "status": record.status,
                "reply_count": record.reply_count,
                "guild_id": record.guild_id,
            })
        return result
