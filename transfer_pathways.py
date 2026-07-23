"""
Shared "realistic transfer destination" data, used by both build_xlsx_v3.py
(Transfer Pathway Countries / Move Type columns) and build_html.py (the full
Top 5 Realistic Transfer Targets modal section).

PATHWAYS maps each of the 32 source countries in the player dataset to two
plausible destination countries, based on real, well-known lower-tier/global
scouting corridors (Balkans -> Austria/Turkey, West & Central Africa ->
Belgium/Portugal, South America -> Argentina/Portugal, Central America/
Caribbean -> Mexico/USA, East/Southeast Asia -> Australia/Saudi Arabia, etc).
These are realistic corridors, not claims about any specific real club's
interest -- the destination clubs generated from this data are fictional,
same as the rest of the sample dataset.
"""

PATHWAYS = {
    "Iceland": ["Norway", "Belgium"],
    "Montenegro": ["Austria", "Turkey"],
    "Croatia": ["Austria", "Turkey"],
    "Serbia": ["Austria", "Turkey"],
    "North Macedonia": ["Austria", "Turkey"],
    "Slovenia": ["Austria", "Poland"],
    "Albania": ["Turkey", "Poland"],
    "Estonia": ["Poland", "Norway"],
    "Latvia": ["Poland", "Norway"],
    "Georgia": ["Turkey", "Poland"],
    "Bolivia": ["Argentina", "Portugal"],
    "Paraguay": ["Argentina", "Portugal"],
    "Ecuador": ["Argentina", "Portugal"],
    "Uruguay": ["Argentina", "Portugal"],
    "Venezuela": ["Argentina", "Portugal"],
    "Ghana": ["Belgium", "Portugal"],
    "Senegal": ["Belgium", "Portugal"],
    "Zambia": ["Belgium", "Portugal"],
    "Ivory Coast": ["Belgium", "Portugal"],
    "Mali": ["Belgium", "Portugal"],
    "Cameroon": ["Belgium", "Portugal"],
    "Japan": ["Belgium", "Australia"],
    "South Korea": ["Belgium", "Saudi Arabia"],
    "Uzbekistan": ["Turkey", "Saudi Arabia"],
    "Thailand": ["Australia", "Saudi Arabia"],
    "Vietnam": ["Australia", "Saudi Arabia"],
    "Iraq": ["Turkey", "Saudi Arabia"],
    "Costa Rica": ["Mexico", "United States"],
    "Honduras": ["Mexico", "United States"],
    "Panama": ["Mexico", "United States"],
    "Jamaica": ["United States", "Mexico"],
    "New Zealand": ["Australia", "United States"],
}

# City-name pools for destination-only countries (used to generate plausible
# fictional destination clubs, same pattern as the source-country city pools).
DEST_CITY_POOLS = {
    "Norway": ["Bergen", "Trondheim", "Stavanger", "Tromso", "Drammen"],
    "Austria": ["Graz", "Linz", "Salzburg", "Innsbruck", "Klagenfurt"],
    "Turkey": ["Izmir", "Bursa", "Antalya", "Konya", "Adana"],
    "Poland": ["Krakow", "Wroclaw", "Poznan", "Gdansk", "Lodz"],
    "Portugal": ["Braga", "Porto", "Coimbra", "Setubal", "Faro"],
    "Belgium": ["Liege", "Ghent", "Antwerp", "Bruges", "Charleroi"],
    "Argentina": ["Rosario", "Cordoba", "Mendoza", "La Plata", "Santa Fe"],
    "Mexico": ["Puebla", "Toluca", "Queretaro", "Leon", "Merida"],
    "United States": ["Columbus", "Nashville", "Portland", "Cincinnati", "Sacramento"],
    "Australia": ["Adelaide", "Newcastle", "Wollongong", "Geelong", "Perth"],
    "Saudi Arabia": ["Jeddah", "Dammam", "Riyadh", "Abha", "Buraidah"],
}

# ---------------------------------------------------------------------------
# Contact info for suggested transfer target clubs.
#
# The 11 destination-only countries above aren't in player_gen.py's source
# TLD/FEDERATION maps (those only cover the 32 source countries), so they're
# defined here. Combined with player_gen's maps below into one lookup that
# covers every country a suggested target club could be in -- domestic
# step-up/lateral candidates (player's own country) and lateral-Europe/etc
# candidates (pathway destination country) alike. Same illustrative-format,
# not-a-verified-address pattern as the player's own clubContactEmail field.
# ---------------------------------------------------------------------------
DEST_TLD = {
    "Norway": "no", "Austria": "at", "Turkey": "com.tr", "Poland": "pl",
    "Portugal": "pt", "Belgium": "be", "Argentina": "com.ar", "Mexico": "mx",
    "United States": "com", "Australia": "com.au", "Saudi Arabia": "sa",
}

DEST_FEDERATION = {
    "Norway": "NFF (Norwegian Football Federation) player registry",
    "Austria": "OFB (Austrian Football Association) player registry",
    "Turkey": "TFF (Turkish Football Federation) player registry",
    "Poland": "PZPN (Polish Football Association) player registry",
    "Portugal": "FPF (Portuguese Football Federation) player registry",
    "Belgium": "RBFA (Royal Belgian Football Association) player registry",
    "Argentina": "AFA (Argentine Football Association) player registry",
    "Mexico": "FMF (Mexican Football Federation) player registry",
    "United States": "U.S. Soccer player registry",
    "Australia": "Football Australia player registry",
    "Saudi Arabia": "SAFF (Saudi Arabian Football Federation) player registry",
}

import player_gen as _player_gen  # noqa: E402  (after DEST_* so no circular-import ordering issues)

COUNTRY_TLD = {**_player_gen.TLD, **DEST_TLD}
COUNTRY_FEDERATION = {**_player_gen.FEDERATION, **DEST_FEDERATION}
