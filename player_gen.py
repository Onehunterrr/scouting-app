import random, re, json

random.seed(42)

TLD = {
    "Iceland":"is","Montenegro":"me","Croatia":"hr","Serbia":"rs","North Macedonia":"mk",
    "Slovenia":"si","Albania":"al","Estonia":"ee","Latvia":"lv","Georgia":"ge",
    "Bolivia":"bo","Paraguay":"py","Ecuador":"ec","Uruguay":"uy","Venezuela":"ve",
    "Ghana":"gh","Senegal":"sn","Zambia":"zm","Ivory Coast":"ci","Mali":"ml","Cameroon":"cm",
    "Japan":"jp","South Korea":"kr","Uzbekistan":"uz","Thailand":"th","Vietnam":"vn","Iraq":"iq",
    "Costa Rica":"cr","Honduras":"hn","Panama":"pa","Jamaica":"jm","New Zealand":"nz",
}

FEDERATION = {
    "Iceland":"KSI (Icelandic Football Association) player registry",
    "Montenegro":"FSCG (Football Association of Montenegro) player registry",
    "Croatia":"HNS (Croatian Football Federation) player registry",
    "Serbia":"FSS (Football Association of Serbia) player registry",
    "North Macedonia":"FFM (Football Federation of North Macedonia) player registry",
    "Slovenia":"NZS (Football Association of Slovenia) player registry",
    "Albania":"FSHF (Albanian Football Association) player registry",
    "Estonia":"EJL (Estonian Football Association) player registry",
    "Latvia":"LFF (Latvian Football Federation) player registry",
    "Georgia":"GFF (Georgian Football Federation) player registry",
    "Bolivia":"FBF (Bolivian Football Federation) player registry",
    "Paraguay":"APF (Paraguayan Football Association) player registry",
    "Ecuador":"FEF (Ecuadorian Football Federation) player registry",
    "Uruguay":"AUF (Uruguayan Football Association) player registry",
    "Venezuela":"FVF (Venezuelan Football Federation) player registry",
    "Ghana":"GFA (Ghana Football Association) player registry",
    "Senegal":"FSF (Senegalese Football Federation) player registry",
    "Zambia":"FAZ (Football Association of Zambia) player registry",
    "Ivory Coast":"FIF (Ivorian Football Federation) player registry",
    "Mali":"FEMAFOOT (Malian Football Federation) player registry",
    "Cameroon":"FECAFOOT (Cameroonian Football Federation) player registry",
    "Japan":"JFA (Japan Football Association) player registry",
    "South Korea":"KFA (Korea Football Association) player registry",
    "Uzbekistan":"UFF (Uzbekistan Football Federation) player registry",
    "Thailand":"FAT (Football Association of Thailand) player registry",
    "Vietnam":"VFF (Vietnam Football Federation) player registry",
    "Iraq":"IFA (Iraq Football Association) player registry",
    "Costa Rica":"FEDEFUTBOL (Costa Rican Football Federation) player registry",
    "Honduras":"FENAFUTH (Honduran Football Federation) player registry",
    "Panama":"FEPAFUT (Panamanian Football Federation) player registry",
    "Jamaica":"JFF (Jamaica Football Federation) player registry",
    "New Zealand":"NZF (New Zealand Football) player registry",
}

# (country, [first names], [last names], [city/club-root names])
NAME_POOLS = {
    "Iceland": (["Jon","Aron","Bjarni","Karl","Andri","Gunnar","Olafur","Stefan","Einar","Vidar"],
                ["Olafsson","Sigurdsson","Gudmundsson","Einarsson","Bjornsson","Karlsson","Petursson","Halldorsson"],
                ["Akureyri","Reykjavik","Selfoss","Keflavik","Hafnarfjordur","Vestmannaeyjar"]),
    "Montenegro": (["Nikola","Vuk","Marko","Petar","Filip","Milos","Igor","Andrej"],
                   ["Vukotic","Jovanovic","Perovic","Nikolic","Radovic","Kovacevic","Ilic"],
                   ["Podgorica","Niksic","Bar","Budva","Danilovgrad","Berane"]),
    "Croatia": (["Ivan","Luka","Toni","Ante","Josip","Marko","Karlo","Domagoj"],
                ["Barisic","Matic","Kovac","Radic","Horvat","Kovacevic","Novak"]  ,
                ["Bijelo Brdo","Belisce","Rudes","Medimurje","Zagreb","Osijek"]),
    "Serbia": (["Aleksandar","Nemanja","Dusan","Milos","Stefan","Vladimir","Uros","Bojan"],
               ["Jovanovic","Stankovic","Petrovic","Krstic","Milic","Ilic","Nikolic"],
               ["Nis","Kraljevo","Dobanovci","Backa Topola","Vojvodina","Cacak"]),
    "North Macedonia": (["Filip","Darko","Igor","Blaze","Goran","Kristijan","Vasil"],
                        ["Trajkovski","Stojanovski","Mitrovski","Angelovski","Ristovski","Naumovski"],
                        ["Stip","Negotino","Skopje","Bitola","Ohrid","Tetovo"]),
    "Slovenia": (["Luka","Jan","Rok","Ziga","Tim","Nejc"],
                 ["Novak","Kranjc","Zupan","Kovac","Krajnc","Horvat"],
                 ["Maribor","Celje","Koper","Kranj","Ljubljana"]),
    "Albania": (["Erion","Ardit","Klodian","Bledi","Ened","Gentian"],
                ["Hoxha","Krasniqi","Bardhi","Duro","Meta","Shala"],
                ["Tirana","Durres","Vlore","Shkoder","Elbasan"]),
    "Estonia": (["Marek","Karl","Rasmus","Andres","Sander","Kevin"],
                ["Tamm","Saar","Kask","Kukk","Mets","Pikk"],
                ["Tallinn","Tartu","Narva","Parnu"]),
    "Latvia": (["Roberts","Kristers","Janis","Arturs","Edgars"],
               ["Berzins","Ozols","Kalns","Vitols","Liepa"],
               ["Riga","Daugavpils","Liepaja","Jelgava"]),
    "Georgia": (["Giorgi","Levan","Nika","Luka","Saba","Irakli"],
                ["Beridze","Kapanadze","Kiknadze","Lomidze","Gelashvili"],
                ["Tbilisi","Batumi","Kutaisi","Rustavi"]),
    "Bolivia": (["Diego","Mateo","Santiago","Nicolas","Franco","Gonzalo"],
                ["Fernandez","Rodriguez","Martinez","Torres","Flores","Vargas"],
                ["La Paz","Santa Cruz","Cochabamba","Sucre","Oruro"]),
    "Paraguay": (["Rodrigo","Sebastian","Emiliano","Ivan","Matias","Cristian"],
                 ["Gonzalez","Lopez","Perez","Sanchez","Ramirez","Cardozo"],
                 ["Asuncion","Ciudad del Este","Encarnacion","Luque"]),
    "Ecuador": (["Carlos","Andres","Bryan","Kevin","Jefferson","Anthony"],
                ["Mendoza","Chavez","Suarez","Zambrano","Quinonez","Preciado"],
                ["Guayaquil","Quito","Cuenca","Manta"]),
    "Uruguay": (["Nicolas","Federico","Agustin","Bruno","Maximiliano","Facundo"],
                ["Silva","Pereira","Rodriguez","Gonzalez","Acosta","Ferreira"],
                ["Montevideo","Salto","Paysandu","Rivera"]),
    "Venezuela": (["Jesus","Wuilker","Yeferson","Ronaldo","Junior","Anderson"],
                  ["Hernandez","Rondon","Martinez","Rojas","Gonzalez","Perez"],
                  ["Caracas","Maracaibo","Valencia","Barquisimeto"]),
    "Ghana": (["Kwame","Kofi","Emmanuel","Ibrahim","Yaw","Kwabena"],
              ["Mensah","Owusu","Boateng","Asante","Appiah","Agyemang"],
              ["Accra","Kumasi","Tamale","Sekondi"]),
    "Senegal": (["Mamadou","Ousmane","Moussa","Abdoulaye","Ibrahima","Cheikh"],
                ["Diop","Sow","Ndiaye","Diallo","Faye","Gueye"],
                ["Dakar","Thies","Saint-Louis","Ziguinchor"]),
    "Zambia": (["Chanda","Mwansa","Kelvin","Given","Emmanuel","Enock"],
               ["Banda","Mwansa","Phiri","Zulu","Tembo","Sakala"],
               ["Lusaka","Ndola","Kitwe","Livingstone"]),
    "Ivory Coast": (["Yves","Didier","Franck","Serge","Wilfried","Max"],
                    ["Toure","Kone","Traore","Diabate","Bamba","Kouassi"],
                    ["Abidjan","Bouake","Yamoussoukro","San Pedro"]),
    "Mali": (["Amadou","Souleymane","Modibo","Sekou","Bakary","Oumar"],
             ["Diarra","Coulibaly","Traore","Keita","Sidibe","Camara"],
             ["Bamako","Sikasso","Mopti","Kayes"]),
    "Cameroon": (["Andre","Jean","Christian","Patrick","Eric","Samuel"],
                 ["Onana","Mbia","Ngoy","Fotso","Njie","Etoo"],
                 ["Yaounde","Douala","Bafoussam","Garoua"]),
    "Japan": (["Haruto","Sota","Ren","Yuto","Kaito","Sora"],
              ["Sato","Suzuki","Takahashi","Tanaka","Watanabe","Ito"],
              ["Osaka","Nagoya","Sapporo","Fukuoka","Sendai"]),
    "South Korea": (["Min-jun","Seo-jun","Ji-ho","Do-yun","Ha-joon","Yoon-seo"],
                     ["Kim","Lee","Park","Choi","Jung","Kang"],
                     ["Busan","Daegu","Incheon","Daejeon","Gwangju"]),
    "Uzbekistan": (["Sardor","Jasur","Bekzod","Sherzod","Otabek","Diyorbek"],
                   ["Karimov","Yusupov","Rashidov","Yuldashev","Tashkentov","Rustamov"],
                   ["Tashkent","Samarkand","Bukhara","Andijan"]),
    "Thailand": (["Somchai","Anon","Chai","Nattapong","Pongsak","Kittipong"],
                 ["Srisawat","Charoensuk","Boonmee","Sanguan","Rattanakosin","Wongsa"],
                 ["Bangkok","Chiang Mai","Khon Kaen","Nonthaburi"]),
    "Vietnam": (["Minh","Duc","Hoang","Nam","Phong","Quang"],
                ["Nguyen","Tran","Le","Pham","Hoang","Vu"],
                ["Hanoi","Da Nang","Hai Phong","Can Tho"]),
    "Iraq": (["Ali","Ahmed","Karim","Hussein","Mustafa","Omar"],
             ["Hassan","Jabbar","Kareem","Abbas","Salim","Mahmoud"],
             ["Baghdad","Basra","Erbil","Najaf"]),
    "Costa Rica": (["Carlos","Luis","Jose","Miguel","Esteban","Randall"],
                   ["Ramirez","Castillo","Morales","Vargas","Solano","Campos"],
                   ["San Jose","Alajuela","Heredia","Cartago"]),
    "Honduras": (["Marlon","Romell","Anthony","Kevin","Wesly","Bryan"],
                 ["Palacios","Martinez","Figueroa","Chirinos","Mejia","Elvir"],
                 ["Tegucigalpa","San Pedro Sula","La Ceiba","Choluteca"]),
    "Panama": (["Andre","Adalberto","Fidel","Jose","Roman","Michael"],
               ["Escobar","Godoy","Torres","Perez","Murillo","Barrios"],
               ["Panama City","Colon","David","Santiago"]),
    "Jamaica": (["Andre","Marlon","Damion","Shamar","Kemar","Devon"],
                ["Campbell","Brown","Reid","Lowe","Bailey","Powell"],
                ["Kingston","Montego Bay","Spanish Town","May Pen"]),
    "New Zealand": (["Liam","Jack","Cole","Ethan","Noah","Callum"],
                    ["Smith","Wilson","Taylor","Brown","Mitchell","Clarke"],
                    ["Auckland","Wellington","Christchurch","Hamilton"]),
}

CLUB_SUFFIX = ["FC","United","City","Athletic","Rangers","Rovers","Wanderers","SC","Town"]
LEAGUE_TEMPLATES = ["{c} Second Division","{c} Regional League","{c} Third Tier - North",
                    "{c} Third Tier - South","{c} Provincial League","{c} Amateur League",
                    "{c} Reserve League","{c} Second Division - East","{c} Second Division - West"]

POSITIONS = ["GK","DF","MF","FW"]

def slug(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower())

def slug_club(city, suffix):
    return slug(f"{city} {suffix}")

# ---------------------------------------------------------------------------
# Position-specific stat generation.
#
# Grounded in real goalkeeper-analytics research (PSxG+/-, save %, distribution
# accuracy, sweeper-keeper defensive actions outside the box -- see FBref's
# #OPA metric and Hudl/StatsBomb goalkeeper-analytics writeups). Outfield
# metrics (progressive passes/carries, tackles+interceptions, goals+assists)
# are kept as-is: they map reasonably well onto real analytics categories
# (progressive actions, ball recoveries, non-penalty end product) and were
# not the identified weak point -- goalkeepers were.
# ---------------------------------------------------------------------------
def gen_stats(position, minutes=None):
    """Returns a dict of raw counting stats appropriate to the position.
    minutes is used to scale goalkeeper-specific volume stats realistically;
    outfield ranges are unchanged from the original season-total design."""
    m90 = (minutes or 1800) / 90.0
    if position == "GK":
        goals, assists = 0, 0
        pp, pc = random.randint(6, 20), random.randint(0, 6)
        ti = random.randint(3, 10)
        saves = round(m90 * random.uniform(2.0, 4.5))
        goals_conceded = round(m90 * random.uniform(1.0, 2.2))
        pass_completion_pct = round(random.uniform(55, 92), 1)
        sweeper_actions = round(m90 * random.uniform(0.1, 1.5), 1)
        clean_sheets = round(m90 * random.uniform(0.15, 0.45))
    elif position == "DF":
        goals = random.randint(0, 3); assists = random.randint(0, 5)
        pp, pc = random.randint(38, 72), random.randint(20, 48)
        ti = random.randint(75, 130)
        saves = goals_conceded = sweeper_actions = clean_sheets = 0
        pass_completion_pct = 0.0
    elif position == "MF":
        goals = random.randint(1, 10); assists = random.randint(4, 14)
        pp, pc = random.randint(105, 178), random.randint(52, 98)
        ti = random.randint(28, 58)
        saves = goals_conceded = sweeper_actions = clean_sheets = 0
        pass_completion_pct = 0.0
    else:  # FW
        goals = random.randint(7, 20); assists = random.randint(1, 9)
        pp, pc = random.randint(28, 68), random.randint(58, 102)
        ti = random.randint(4, 16)
        saves = goals_conceded = sweeper_actions = clean_sheets = 0
        pass_completion_pct = 0.0
    return {
        "goals": goals, "assists": assists, "progPasses": pp, "progCarries": pc, "tklInt": ti,
        "saves": saves, "goalsConceded": goals_conceded,
        "passCompletionPct": pass_completion_pct, "sweeperActions": sweeper_actions,
        "cleanSheets": clean_sheets,
    }

COUNTRIES = list(NAME_POOLS.keys())

def generate_players(n, used_names, date_str, rng=None):
    """Generate n new fictional players, avoiding name collisions with used_names
    (a set that will be mutated in place). date_str is used for dateAdded/lastUpdated."""
    r = rng or random
    out = []
    for i in range(n):
        country = COUNTRIES[r.randrange(len(COUNTRIES))]
        firsts, lasts, cities = NAME_POOLS[country]
        tld = TLD[country]
        federation = FEDERATION[country]

        name = None
        for _try in range(40):
            cand = f"{r.choice(firsts)} {r.choice(lasts)}"
            if cand not in used_names:
                name = cand
                used_names.add(name)
                break
        if name is None:
            cand = f"{r.choice(firsts)} {r.choice(lasts)} {r.randint(2,99)}"
            used_names.add(cand)
            name = cand

        position = r.choices(POSITIONS, weights=[0.15,0.30,0.30,0.25])[0]
        age = r.randint(17, 26)
        tier = r.choices([2,3,4], weights=[0.45,0.35,0.20])[0]
        minutes = r.randint(800, 2700)
        stats = gen_stats(position, minutes)
        city = r.choice(cities)
        club = f"{city} {r.choice(CLUB_SUFFIX)}"
        league = r.choice(LEAGUE_TEMPLATES).format(c=country)
        market_value = 0 if r.random() < 0.25 else r.randint(8000, 150000)
        has_agent = r.choices(["No","Yes","Unknown"], weights=[0.45,0.25,0.30])[0]
        contract_expires = r.randint(2026, 2029)
        club_email = f"info@{slug(club)}.{tld}"

        if age < 18:
            route = "Club youth/academy office ONLY - player is a minor, do not contact directly"
        elif has_agent == "Yes":
            route = "Go through the player's agent - ask the club to confirm representation first"
        else:
            route = "Club sporting director / first-team office"

        out.append({
            "name": name, "country": country, "league": league, "tier": tier, "club": club,
            "position": position, "age": age, "minutes": minutes,
            "goals": stats["goals"], "assists": stats["assists"],
            "progPasses": stats["progPasses"], "progCarries": stats["progCarries"], "tklInt": stats["tklInt"],
            "saves": stats["saves"], "goalsConceded": stats["goalsConceded"],
            "passCompletionPct": stats["passCompletionPct"], "sweeperActions": stats["sweeperActions"],
            "cleanSheets": stats["cleanSheets"],
            "marketValue": market_value,
            "hasAgent": has_agent, "contractExpires": contract_expires,
            "clubContactEmail": club_email, "contactRoute": route, "federationRegistry": federation,
            "dateAdded": date_str, "lastUpdated": date_str,
        })
    return out


if __name__ == "__main__":
    # Standalone run: regenerate the original 100-player baseline (unchanged behavior),
    # now including dateAdded/lastUpdated stamped with today's date.
    import sys, datetime
    random.seed(42)
    today = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    target = 100
    per_country_counts = {c: 0 for c in COUNTRIES}
    for i in range(target):
        per_country_counts[COUNTRIES[i % len(COUNTRIES)]] += 1

    used_names = set()
    players = []
    for country, count in per_country_counts.items():
        firsts, lasts, cities = NAME_POOLS[country]
        tld = TLD[country]
        federation = FEDERATION[country]
        for _ in range(count):
            for _try in range(20):
                name = f"{random.choice(firsts)} {random.choice(lasts)}"
                if name not in used_names:
                    used_names.add(name)
                    break
            position = random.choices(POSITIONS, weights=[0.15,0.30,0.30,0.25])[0]
            age = random.randint(17, 26)
            tier = random.choices([2,3,4], weights=[0.45,0.35,0.20])[0]
            minutes = random.randint(800, 2700)
            stats = gen_stats(position, minutes)
            city = random.choice(cities)
            club = f"{city} {random.choice(CLUB_SUFFIX)}"
            league = random.choice(LEAGUE_TEMPLATES).format(c=country)
            market_value = 0 if random.random() < 0.25 else random.randint(8000, 150000)
            has_agent = random.choices(["No","Yes","Unknown"], weights=[0.45,0.25,0.30])[0]
            contract_expires = random.randint(2026, 2029)
            club_email = f"info@{slug(club)}.{tld}"

            if age < 18:
                route = "Club youth/academy office ONLY - player is a minor, do not contact directly"
            elif has_agent == "Yes":
                route = "Go through the player's agent - ask the club to confirm representation first"
            else:
                route = "Club sporting director / first-team office"

            players.append({
                "name": name, "country": country, "league": league, "tier": tier, "club": club,
                "position": position, "age": age, "minutes": minutes,
                "goals": stats["goals"], "assists": stats["assists"],
                "progPasses": stats["progPasses"], "progCarries": stats["progCarries"], "tklInt": stats["tklInt"],
                "saves": stats["saves"], "goalsConceded": stats["goalsConceded"],
                "passCompletionPct": stats["passCompletionPct"], "sweeperActions": stats["sweeperActions"],
                "cleanSheets": stats["cleanSheets"],
                "marketValue": market_value,
                "hasAgent": has_agent, "contractExpires": contract_expires,
                "clubContactEmail": club_email, "contactRoute": route, "federationRegistry": federation,
                "dateAdded": today, "lastUpdated": today,
            })

    random.shuffle(players)
    assert len(players) == 100

    with open("players_100.json", "w") as f:
        json.dump(players, f, indent=2)

    print(f"generated {len(players)} players across {len(COUNTRIES)} countries, dateAdded={today}")
