"""
One-time script: adds Uudenmaan alueen yhdistykset to the organiser taxonomy.
Run once before starting the bot: python seed_organisers.py
"""
from config import load_config
import db

ORGANISERS = [
    "Aaltovihreät ry",
    "Espoon tieteen ja teknologian vihreät ry",
    "Espoon Vihreät Naiset ry",
    "Espoon Vihreät ry",
    "Hangon Vihreät – Hangö Gröna ry",
    "Hyvinkään Vihreät",
    "Inkoon Vihreät ry",
    "Itä-Uudenmaan Vihreät Naiset ry",
    "Järvenpään Vihreät ry",
    "Karkkilan Vihreät ry",
    "Kauniaisten Vihreät – De Gröna i Grankulla ry",
    "Keravan Vihreät ry",
    "Keski- ja Itä-Vantaan Vihreät ry",
    "Kirkkonummen Vihreät ry",
    "Korso-Koivukylän Vihreät ry",
    "Lohjan Vihreät ry",
    "Loviisan seudun vihreät ry",
    "Länsi-Vantaan Vihreät ry",
    "Nurmijärven vihreät ry",
    "Porvoon Vihreät – De Gröna i Borgå",
    "Raaseporin Vihreät ry",
    "Sipoon Vihreät – De Gröna i Sibbo ry",
    "Siuntion Vihreät ry",
    "Tuusulan vihreät ry",
    "Uudenmaan Vihreät nuoret ry",
    "Vantaan Ikivihreät ry",
    "Vantaan tieteen ja teknologian vihreät ry",
    "Vantaan Vihreät Naiset ry",
    "Vantaan Vihreät ry",
    "Vihdin Vihreät ry",
    "Vihreä Verkko ry",
]

cfg = load_config()
db.init_db(cfg.database_url, cfg.encryption_key)

for org in ORGANISERS:
    db.add_taxonomy_term("organiser", org)
    print(f"  + {org}")

print(f"\nValmis! {len(ORGANISERS)} järjestäjää lisätty.")
