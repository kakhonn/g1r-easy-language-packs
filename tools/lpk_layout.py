"""Where `lpk.py split` puts each row: the pack layout.

`split` builds its rows per sheet (01_red_blue ... 13_dex_gen3, see
lpk.SPLIT_FILES); `organize` turns those sheets into {relative path: rows}.
Stdlib only, like lpk.py.

Layouts:
  flat       the 13 sheets in the pack root (the original layout)
  organized  one folder per generation, and inside it one file per place:
             Towns/ (T##, C1## in Hoenn), Routes/ (R##, W1## underwater),
             Dungeons/ (D##), Frontier/ (F##, Emerald), plus System/,
             Names/, Pokedex/, Descriptions/, Engine/. Each base game's
             additions sit in a subfolder (G1/Yellow, G2/Crystal,
             G3/RSE/Emerald); text FireRed/LeafGreen and Ruby/Sapphire/
             Emerald share goes to G3/Shared/ once.

Where a place goes comes from the game itself: Gen 1 lines by their script
label (a fixed table of map codes), Gen 2 lines by tracing map events through
scripts to the text and taking the map's Town Map landmark, Gen 3 lines by
their label or the same tracing, and the map's region-map section. Gen 2 and
Gen 3 read the imported data under <root>/<version>/data/generated; a sheet
whose game data isn't there goes to its generation folder whole
(GENERATION_PATHS).

The place codes nod to the map file names of the original games; they are
labels only.
"""
import collections
import json
import os
import re

LAYOUTS = ("organized", "flat")
DEFAULT_LAYOUT = "organized"

# a whole sheet's path when its places can't be worked out (no game data)
GENERATION_PATHS = {
    "01_red_blue": "G1/01_red_blue.csv",
    "02_yellow_exclusive": "G1/Yellow/02_yellow_exclusive.csv",
    "08_names_gen1": "G1/08_names_gen1.csv",
    "11_dex_gen1": "G1/11_dex_gen1.csv",
    "03_gold_silver": "G2/03_gold_silver.csv",
    "04_crystal_exclusive": "G2/Crystal/04_crystal_exclusive.csv",
    "09_names_gen2": "G2/09_names_gen2.csv",
    "12_dex_gen2": "G2/12_dex_gen2.csv",
    "05_firered_leafgreen": "G3/FRLG/05_firered_leafgreen.csv",
    "06_ruby_sapphire": "G3/RSE/06_ruby_sapphire.csv",
    "07_emerald_exclusive": "G3/RSE/Emerald/07_emerald_exclusive.csv",
    "10_names_gen3": "G3/10_names_gen3.csv",
    "13_dex_gen3": "G3/13_dex_gen3.csv",
}
ORGANIZED_PATHS = GENERATION_PATHS  # older name

# folder names inside a generation (or a game's subfolder)
FOLDERS = {"towns": "Towns", "routes": "Routes", "dungeons": "Dungeons", "frontier": "Frontier",
           "system": "System", "names": "Names", "pokedex": "Pokedex", "descriptions": "Descriptions",
           "engine": "Engine", "shared": "Shared"}


def sheet_path(sheet, layout):
    """Where a whole sheet goes: the pack root (flat) or its generation folder."""
    if layout == "flat":
        return sheet + ".csv"
    return GENERATION_PATHS[sheet]


def organize(sheets, layout, root=None):
    """{sheet: rows} (in lpk.SPLIT_FILES order) -> {relative path: rows}.
    `root` holds the imported games (<root>/<version>/data/generated). Rows
    keep their order within a file; a row's text is never changed, except that
    a row FRLG and Ruby/Sapphire/Emerald both define is merged into one
    (versions joined, both games' notes kept)."""
    out = collections.OrderedDict()

    def put(path, row):
        out.setdefault(path, []).append(row)

    if layout == "flat":
        for sheet, rows in sheets.items():
            out.setdefault(sheet + ".csv", []).extend(rows)   # every sheet, even an empty one
        return out
    sheets = collections.OrderedDict((k, list(v)) for k, v in sheets.items())
    get = lambda s: sheets.get(s, [])

    # ---- Gen 1 (labels; needs no game data) ----
    for row in get("01_red_blue"):
        put(_g1_file(row, "G1", exclude="CeruleanMelaniesHouse"), row)
    for row in get("02_yellow_exclusive"):
        put(_g1_file(row, "G1/Yellow", exclude="CeruleanTradeHouse"), row)
    for row in get("08_names_gen1"):
        put(_f("G1", "names", "species.csv"), row)
    for row in get("11_dex_gen1"):
        put(_dex1_file(row), row)

    # ---- Gen 2 (traced through the imported Gold/Silver and Crystal data) ----
    for sheet, versions, base in (("03_gold_silver", ("gold", "silver"), "G2"),
                                  ("04_crystal_exclusive", ("crystal",), "G2/Crystal")):
        g2 = _Gen2.load(root, versions)
        for row in get(sheet):
            put(g2.file(row, base) if g2 else GENERATION_PATHS[sheet], row)
    for row in get("09_names_gen2"):
        put(_f("G2", "names", "species.csv"), row)
    for row in get("12_dex_gen2"):
        put(_dex2_file(row), row)

    # ---- Gen 3 ----
    rs = _Gen3.load(root, ("ruby", "sapphire"), "rs")
    fr = _Gen3.load(root, ("firered", "leafgreen"), "frlg")
    em = _Gen3.load(root, ("emerald",), "emerald")
    rs_rows, fr_rows, em_rows = get("06_ruby_sapphire"), get("05_firered_leafgreen"), get("07_emerald_exclusive")
    rs_files = [rs.rs_file(r) if rs else GENERATION_PATHS["06_ruby_sapphire"] for r in rs_rows]
    shared, gone = _shared_g3(fr_rows, rs_rows, rs_files, em_rows)
    for rel, row in shared:
        put(rel, row)
    for row, rel in zip(rs_rows, rs_files):
        if id(row) not in gone:
            put(rel, row)
    for row in fr_rows:
        if id(row) not in gone:
            put(fr.frlg_file(row) if fr else _frlg_fallback(row), row)
    for row in em_rows:
        if id(row) not in gone:
            put(em.emerald_file(row) if em else GENERATION_PATHS["07_emerald_exclusive"], row)
    for row in get("10_names_gen3"):
        put(_f("G3/Shared", "names", "species.csv"), row)
    for row in get("13_dex_gen3"):
        put(_dex3_file(row), row)
    return out


def _f(base, folder, name):
    return f"{base}/{FOLDERS[folder]}/{name}"


def _first_label(notes):
    """Gen 1: the first label in a row's notes."""
    return re.split(r"[ ,;]+", re.sub(r"\(\+\d+\)", "", notes).strip())[0].lstrip("_")


def _stem_file(base, stem):
    """A place stem (T01_pallet, R12, C103_mauville, W124, F02_battle_dome) or
    a path under the base folder (System/radio.csv) -> relative path."""
    if stem.endswith(".csv"):
        head, _, rest = stem.partition("/")
        key = {"System": "system", "Names": "names", "Pokedex": "pokedex", "Descriptions": "descriptions",
               "Engine": "engine"}.get(head)
        return f"{base}/{FOLDERS[key]}/{rest}" if key else f"{base}/{stem}"
    folder = {"T": "towns", "C": "towns", "R": "routes", "W": "routes", "D": "dungeons", "F": "frontier"}[stem[0]]
    return _f(base, folder, stem + ".csv")


# =============================================================== Gen 1
# Map label -> code. Towns T00-T11, routes R01-R25, dungeons D01-D16; a few
# maps sit where they are in the final game rather than where their number
# says (Victory Road, Rock Tunnel 1F, Mansion 1F, Silph Co. 1F, Safari gate).
G1_CODES = {
    "AgathasRoom": "D14", "BikeShop": "T04", "BillsHouse": "R25", "BluesHouse": "T01", "BrunosRoom": "D14",
    "CeladonChiefHouse": "T07", "CeladonCity": "T07", "CeladonDiner": "T07", "CeladonGym": "T07",
    "CeladonHotel": "T07", "CeladonMansion1F": "T07", "CeladonMansion2F": "T07", "CeladonMansion3F": "T07",
    "CeladonMansionRoof": "T07", "CeladonMansionRoofHouse": "T07", "CeladonMart1F": "T07",
    "CeladonMart2F": "T07", "CeladonMart3F": "T07", "CeladonMart4F": "T07", "CeladonMart5F": "T07",
    "CeladonMartElevator": "T07", "CeladonMartRoof": "T07", "CeladonPokecenter": "T07",
    "CeruleanBadgeHouse": "T04", "CeruleanCave1F": "T04", "CeruleanCave2F": "D13", "CeruleanCaveB1F": "D13",
    "CeruleanCity": "T04", "CeruleanGym": "T04", "CeruleanMart": "T04", "CeruleanMelaniesHouse": "T04",
    "CeruleanPokecenter": "T04", "CeruleanTradeHouse": "T04", "CeruleanTrashedHouse": "T04",
    "ChampionsRoom": "D14", "CinnabarGym": "T09", "CinnabarIsland": "T09", "CinnabarLab": "T09",
    "CinnabarLabFossilRoom": "T09", "CinnabarLabMetronomeRoom": "T09", "CinnabarLabTradeRoom": "T09",
    "CinnabarMart": "T09", "CinnabarPokecenter": "T09", "Colosseum": "T00", "CopycatsHouse1F": "T11",
    "CopycatsHouse2F": "T11", "Daycare": "R05", "DiglettsCave": "D07", "DiglettsCaveRoute11": "R11",
    "DiglettsCaveRoute2": "R02", "FightingDojo": "T11", "FuchsiaBillsGrandpasHouse": "T08",
    "FuchsiaCity": "T08", "FuchsiaGoodRodHouse": "T08", "FuchsiaGym": "T08", "FuchsiaMart": "T08",
    "FuchsiaMeetingRoom": "T08", "FuchsiaPokecenter": "T08", "GameCorner": "T07", "GameCornerPrizeRoom": "T07",
    "HallOfFame": "D14", "IndigoPlateau": "T10", "IndigoPlateauLobby": "T10", "LancesRoom": "D14",
    "LavenderCuboneHouse": "T05", "LavenderMart": "T05", "LavenderPokecenter": "T05", "LavenderTown": "T05",
    "LoreleisRoom": "D14", "MrFujisHouse": "T05", "MrPsychicsHouse": "T11", "MtMoon1F": "D02",
    "MtMoonB1F": "D02", "MtMoonB2F": "D02", "MtMoonPokecenter": "R04", "Museum1F": "T03", "Museum2F": "T03",
    "NameRatersHouse": "T05", "OaksLab": "T01", "PalletTown": "T01", "PewterCity": "T03", "PewterGym": "T03",
    "PewterMart": "T03", "PewterNidoranHouse": "T03", "PewterPokecenter": "T03", "PewterSpeechHouse": "T03",
    "PokemonFanClub": "T06", "PokemonMansion1F": "D11", "PokemonMansion2F": "D11", "PokemonMansion3F": "D11",
    "PokemonMansionB1F": "D11", "PokemonTower1F": "T05", "PokemonTower2F": "T05", "PokemonTower3F": "T05",
    "PokemonTower4F": "T05", "PokemonTower5F": "T05", "PokemonTower6F": "T05", "PokemonTower7F": "T05",
    "PowerPlant": "R10", "RedsHouse1F": "T01", "RedsHouse2F": "T01", "RockTunnel1F": "D15",
    "RockTunnelB1F": "D15", "RockTunnelPokecenter": "R10", "RocketHideoutB1F": "D09",
    "RocketHideoutB2F": "D09", "RocketHideoutB3F": "D09", "RocketHideoutB4F": "D09",
    "RocketHideoutElevator": "D09", "Route1": "R01", "Route10": "R10", "Route11": "R11",
    "Route11Gate1F": "R11", "Route11Gate2F": "R11", "Route12": "R12", "Route12Gate1F": "R12",
    "Route12Gate2F": "R12", "Route12SuperRodHouse": "R12", "Route13": "R13", "Route14": "R14",
    "Route15": "R15", "Route15Gate1F": "R15", "Route15Gate2F": "R15", "Route16": "R16",
    "Route16FlyHouse": "R16", "Route16Gate1F": "R16", "Route16Gate2F": "R16", "Route17": "R17",
    "Route18": "R18", "Route18Gate1F": "R18", "Route18Gate2F": "R18", "Route19": "R19", "Route2": "R02",
    "Route20": "R20", "Route21": "R21", "Route22": "R22", "Route22Gate": "R22", "Route23": "R23",
    "Route24": "R24", "Route25": "R25", "Route2Gate": "R02", "Route2TradeHouse": "R02", "Route3": "R03",
    "Route4": "R04", "Route5": "R05", "Route5Gate": "R05", "Route6": "R06", "Route6Gate": "R06",
    "Route7": "R07", "Route7Gate": "R07", "Route8": "R08", "Route8Gate": "R08", "Route9": "R09",
    "SSAnne1F": "D03", "SSAnne1FRooms": "D03", "SSAnne2F": "D03", "SSAnne2FRooms": "D03", "SSAnne3F": "D03",
    "SSAnneB1F": "D03", "SSAnneB1FRooms": "D03", "SSAnneBow": "D03", "SSAnneCaptainsRoom": "D03",
    "SSAnneKitchen": "D03", "SafariZoneCenter": "D12", "SafariZoneCenterRestHouse": "D12",
    "SafariZoneEast": "D12", "SafariZoneEastRestHouse": "D12", "SafariZoneGate": "D12",
    "SafariZoneNorth": "D12", "SafariZoneNorthRestHouse": "D12", "SafariZoneSecretHouse": "D12",
    "SafariZoneWest": "D12", "SafariZoneWestRestHouse": "D12", "SaffronCity": "T11", "SaffronGym": "T11",
    "SaffronMart": "T11", "SaffronPidgeyHouse": "T11", "SaffronPokecenter": "T11", "SeafoamIslands1F": "R20",
    "SeafoamIslandsB1F": "D16", "SeafoamIslandsB2F": "D16", "SeafoamIslandsB3F": "D16",
    "SeafoamIslandsB4F": "D16", "SilphCo10F": "D10", "SilphCo11F": "D10", "SilphCo1F": "D10",
    "SilphCo2F": "D10", "SilphCo3F": "D10", "SilphCo4F": "D10", "SilphCo5F": "D10", "SilphCo6F": "D10",
    "SilphCo7F": "D10", "SilphCo8F": "D10", "SilphCo9F": "D10", "SilphCoElevator": "D10",
    "SummerBeachHouse": "R19", "TradeCenter": "T00", "UndergroundPathNorthSouth": "D04",
    "UndergroundPathRoute5": "R05", "UndergroundPathRoute6": "R06", "UndergroundPathRoute7": "R07",
    "UndergroundPathRoute7Copy": "R07", "UndergroundPathRoute8": "R08", "UndergroundPathWestEast": "D06",
    "VermilionCity": "T06", "VermilionDock": "T06", "VermilionGym": "T06", "VermilionMart": "T06",
    "VermilionOldRodHouse": "T06", "VermilionPidgeyHouse": "T06", "VermilionPokecenter": "T06",
    "VermilionTradeHouse": "T06", "VictoryRoad1F": "D08", "VictoryRoad2F": "D08", "VictoryRoad3F": "D08",
    "ViridianCity": "T02", "ViridianForest": "D01", "ViridianForestNorthGate": "R02",
    "ViridianForestSouthGate": "R02", "ViridianGym": "T02", "ViridianMart": "T02",
    "ViridianNicknameHouse": "T02", "ViridianPokecenter": "T02", "ViridianSchoolHouse": "T02",
    "WardensHouse": "T08",
}
G1_SHORT = {
    "T00": "cable_club", "T01": "pallet", "T02": "viridian", "T03": "pewter", "T04": "cerulean",
    "T05": "lavender", "T06": "vermilion", "T07": "celadon", "T08": "fuchsia", "T09": "cinnabar",
    "T10": "indigo_plateau", "T11": "saffron",
    "D01": "viridian_forest", "D02": "mt_moon", "D03": "ss_anne", "D04": "underground_path_ns",
    "D05": "unused", "D06": "underground_path_we", "D07": "digletts_cave", "D08": "victory_road",
    "D09": "rocket_hideout", "D10": "silph_co", "D11": "pokemon_mansion", "D12": "safari_zone",
    "D13": "cerulean_cave", "D14": "pokemon_league", "D15": "rock_tunnel", "D16": "seafoam_islands",
}
# labels with no map prefix, but an obvious home (first match wins)
G1_NONMAP = [
    (r"OakSpeech|IntroducePlayer|IntroduceRival|YourNameIs|HisNameIs", "intro.csv"),
    # Yellow
    (r"MtMoonJessieJames", "D02"), (r"PokemonTowerJessieJames", "T05"),
    (r"RocketHideoutJessieJames", "D09"), (r"SilphCoJessieJames", "D10"),
    (r"Melanie", "T04"), (r"OfficerJenny|FanClub", "T06"), (r"CeladonMansion1Text", "T07"),
    (r"SafariZoneLowCost", "D12"),
    (r"Pikachu(?!sBeach)|ExpressionText|LooksContentText|RefusingText", "System/pikachu.csv"),
    (r"Printer_|Diploma|NotEnoughMemory|^NoPokemonText|DontHavePokemon", "System/printer.csv"),
    (r"OakLabEmail|RedBedroomSNES", "T01"), (r"ViridianBlackboard|ViridianSchool", "T02"),
    (r"AerodactylFossil|KabutopsFossil|TM34Explanation", "T03"), (r"TM24Explanation", "T06"),
    (r"TM21Explanation|SlotMachine|Coin|Prize", "T07"), (r"CinnabarQuiz", "T09"),
    (r"IndigoPlateau", "T10"), (r"TM46Explanation", "T11"),
    (r"MtMoon", "D02"), (r"RocketHide", "D09"), (r"CardKey", "D10"),
    (r"Safari|ThrewBait|ThrewRock|TimesUp", "D12"), (r"Mewtwo", "D13"),
    (r"Agatha|Bruno|Lorelei|Lance", "D14"),
    (r"GymStatue|SaffronGateGuard|OaksAide", "shared.csv"),
]
G1_SYSTEM = [  # first match wins; battle catches the rest
    ("pokedex", r"Dex"),
    ("trades_link", r"Trade|CableClub|LinkCable|LinkBattle|ConnectCable|WrongMon|Thanks\dText|NoTrade|WannaTrade"
                    r"|AfterTrade|TwoOptionMenuStrings\.Trade"),
    ("pc_storage", r"PC|Box|Deposit|Withdr|Release|Stored|SentTo|MonIsTakenOut|CantTakeMon|NothingStored"
                   r"|NothingToDeposit|WhatToDeposit|WhatToWithdraw|ChooseABox|NoRoomToStore|WhenYouChangeBox"
                   r"|OnceReleased|WithdrewItem"),
    ("marts_centers", r"Pokemart|PokemonCenter|PokeCenter|MartSign|ShallWeHeal|PokemonFightingFit"
                      r"|PokemonSellingGreeting|NeedYourPokemon|WarpToLastPokemonCenter|PlayerBlackedOut"),
    ("save_menus", r"Save|^SavingText|ClearSaveData|FileDataDestroyed|OlderFile|PartyMenu|CancelTextString"
                   r"|Nickname|TextIDError|DrawText|ContChar|JustAMoment|WhatText|WaitingText|PushStart"
                   r"|OKExclamation|OhFineThen|WhatDoYouWant"),
    ("field_overworld", r"Cut|Strength|Surf|Flash|Fly|Teleport|DugAHole|Boulder|Bicycle|Cycling|NoRunning"
                        r"|Elevator|WhichFloor|CurrentTooFast|CannotGetOff|NoSurfing|FoundHiddenItem"
                        r"|HiddenItemBagFull|FoundItem|FoundHiddenCoins|DroppedHiddenCoins|NothingHere|Bookcase"
                        r"|Magazines|PokemonStuff|TownMap|VendingMachine|DiglettSculpture|TurnPage|TMNotebook"
                        r"|SwitchOn|TurnedOnPC|UnusedBenchGuy|PlayedFlute|FluteWokeUp|GotMap|CantMove"
                        r"|RepelWoreOff|NewBadgeRequired|GotOn|GotOff|ShroudedInMist|CannotUseItemsHere"
                        r"|ItemsCantBeUsedHere|NoMoreRoomForItem|CantCarryMore|OopsYouDontHaveEnoughRoom"
                        r"|SSTicket|GotMon|HereYouGo|GetOut|LinedUp"),
    ("items_bag", r"TeachingHMs|ItemUse|Ball|Potion|Antidote|Awakening|BurnHeal|IceHeal|FullHeal|ParlyzHeal"
                  r"|Revive|PP|Vitamin|Itemfinder|ItsABite|NoNibble|HookedMon|BootedUp|TeachMachineMove"
                  r"|MonCannotLearnMachineMove|HMCantDelete|IsItOKToToss|ThrewAwayItem|WhatToToss"
                  r"|TooImportantToToss|ItemWasStored|NotHealthyEnough|NoEffectText|RaisePP|RestorePP"
                  r"|NothingHappened|NotThisTime"),
    ("learn_evolve", r"Learn|Forget|ForgotAnd|OneTwoAnd|Poof|IsEvolving|Evolved|StoppedEvolving|GrewLevel"
                     r"|ExpPoints|WithExpAll|GainedText|AbandonLearning|DidNotLearn|TryingToLearn|AlreadyKnows"),
    ("battle", r".*"),
]
G1_ENGINE = [  # engine strings by source file (unscoped: they apply to every game)
    ("online_link", r"src/(online|link)/|TradeAnim"),
    ("battle", r"src/battle/|Evolution"),
    ("mod_manager", r"src/mods/|QuarantineReport"),
    ("options", r"OptionsMenu|BindingsMenu|PaletteScreen|ShaderFX"),
    ("overworld", r"src/world/|src/script/"),
    ("screens_misc", r"Title|Credits|Diploma|HallOfFame|OakSpeech|IntroMovie|DiscordPresence|Game\.lua|Printer"
                     r"|SurfingMinigame|SlotMachine|PrizeCounter|union/gen1/Text"),
    ("menus", r".*"),
]
_G1_SORTED = sorted(G1_CODES, key=len, reverse=True)


def _g1_code_file(base, code):
    name = code if code[0] == "R" else f"{code}_{G1_SHORT[code]}"
    return _stem_file(base, name)


def _g1_file(row, base, exclude):
    kind, notes = row[0], row[6]
    if kind == "dialogue":
        lab = _first_label(notes)
        for m in _G1_SORTED:
            if m != exclude and lab.startswith(m):
                return _g1_code_file(base, G1_CODES[m])
        for pat, dest in G1_NONMAP:
            if re.search(pat, lab):
                return _stem_file(base, dest) if dest.endswith(".csv") else _g1_code_file(base, dest)
        for name, pat in G1_SYSTEM:
            if re.search(pat, lab):
                return _f(base, "system", name + ".csv")
    if kind == "strings":
        src = re.sub(r":\d+.*$", "", notes)
        name = next(n for n, pat in G1_ENGINE if re.search(pat, src))
        return f"{FOLDERS['engine']}/{name}.csv"
    return _f(base, "names", kind + ".csv")


def _versions(row):
    return set(row[5].split())


def _dex1_file(row):
    if row[0] == "dex_kind":
        return _f("G1", "pokedex", "categories.csv")
    if _versions(row) == {"yellow"}:
        return _f("G1/Yellow", "pokedex", "entries.csv")
    return _f("G1", "pokedex", "entries.csv")


# =============================================================== Gen 2
G2_TOWNS = {
    "SPECIAL": "T00_cable_club",
    "PALLET TOWN": "T01_pallet", "VIRIDIAN CITY": "T02_viridian", "PEWTER CITY": "T03_pewter",
    "CERULEAN CITY": "T04_cerulean", "LAVENDER TOWN": "T05_lavender", "LAV RADIO TOWER": "T05_lavender",
    "VERMILION CITY": "T06_vermilion", "CELADON CITY": "T07_celadon", "FUCHSIA CITY": "T08_fuchsia",
    "CINNABAR ISLAND": "T09_cinnabar", "INDIGO PLATEAU": "T10_indigo_plateau", "SAFFRON CITY": "T11_saffron",
    "NEW BARK TOWN": "T20_new_bark", "CHERRYGROVE CITY": "T21_cherrygrove", "VIOLET CITY": "T22_violet",
    "AZALEA TOWN": "T23_azalea", "CIANWOOD CITY": "T24_cianwood", "GOLDENROD CITY": "T25_goldenrod",
    "OLIVINE CITY": "T26_olivine", "ECRUTEAK CITY": "T27_ecruteak", "MAHOGANY TOWN": "T28_mahogany",
    "LAKE OF RAGE": "T29_lake_of_rage", "BLACKTHORN CITY": "T30_blackthorn",
}
# Johto dungeons are numbered in rough encounter order from D17; Kanto keeps Gen 1's codes
G2_DUNGEONS = {
    "MT.MOON": "D02_mt_moon", "DIGLETT's CAVE": "D07_digletts_cave", "DIGLETT'S CAVE": "D07_digletts_cave",
    "VICTORY ROAD": "D08_victory_road",
    "ROCK TUNNEL": "D15_rock_tunnel", "SEAFOAM ISLANDS": "D16_seafoam_islands",
    "UNDERGROUND": "D04_underground_path",
    "SPROUT TOWER": "D17_sprout_tower", "RUINS OF ALPH": "D18_ruins_of_alph", "UNION CAVE": "D19_union_cave",
    "SLOWPOKE WELL": "D20_slowpoke_well", "ILEX FOREST": "D21_ilex_forest",
    "NATIONAL PARK": "D23_national_park", "BURNED TOWER": "D24_burned_tower", "LIGHTHOUSE": "D25_lighthouse",
    "MT.MORTAR": "D26_mt_mortar", "RADIO TOWER": "D28_radio_tower", "ICE PATH": "D29_ice_path",
    "DRAGON'S DEN": "D30_dragons_den", "DARK CAVE": "D31_dark_cave", "WHIRL ISLANDS": "D32_whirl_islands",
    "TIN TOWER": "D33_tin_tower", "TOHJO FALLS": "D34_tohjo_falls", "FAST SHIP": "D35_fast_ship",
    "SILVER CAVE": "D36_silver_cave",
}
G2_MAP_OVERRIDES = [  # map id pattern -> stem (checked before landmarks)
    (r"TEAM_ROCKET_BASE", "D27_team_rocket_base"),
    (r"GOLDENROD_UNDERGROUND", "D22_goldenrod_underground"),
    (r"(WILLS|KOGAS|BRUNOS|KARENS|LANCES)_ROOM|HALL_OF_FAME", "D14_pokemon_league"),
    (r"SILVER_CAVE_(OUTSIDE|POKECENTER)", "T31_mt_silver"),
    (r"POWER_PLANT", "R10"),
    (r"BATTLE_TOWER", "D37_battle_tower"),  # Crystal; includes its Route 40 gate
]
# labelled lines no map reaches: places first, then System families
G2_LABELS = [
    (r"^NameRater", "T25_goldenrod"), (r"^(BargainShop|HerbShop|HerbalLady)", "D22_goldenrod_underground"),
    (r"^Pharmacy|Photo", "T24_cianwood"), (r"^Deleter|AskDeleteMove", "T30_blackthorn"),
    (r"KarpGuru|MagikarpGuru", "T29_lake_of_rage"),
    (r"Contest|RemainingTime|SentTrophyHome", "D23_national_park"),
    (r"^OakText\d", "intro.csv"),
    (r"^(OPT_|PnP_|LC_|Ben|Fern|RocketRadio)|LuckyNumber", "System/radio.csv"),
    (r"^(OakTime|Timeset|Clock|DST|Password)|ThePasswordIs|TimeAskOkay|MainMenuTimeUnknown", "System/clock.csv"),
    (r"^(Phone|Pokegear|Gear)", "System/phone.csv"),
    (r"^(Mom|Sent(All|Half|Some)ToMom)", "System/mom.csv"),
    (r"^(OakRating|OakPC)|NewDexData", "System/pokedex.csv"),
    (r"^(DayCare|Breed|Text_Breed)|Compatibility|LeftWithDayCare|YouNeedTwoMonForBreeding|IllRaiseYourMon"
     r"|WhatShouldIRaise|OnlyOneMon|CantAcceptEgg|^RemoveMailText|LastHealthyMon|ComeBackLater|AreWeGeniuses"
     r"|YourMonHasGrown|PerfectHeresYourMon|GotBackMon|BackAlready|HaveNoRoom|^NotEnoughMoneyText|^OhFineThenText"
     r"|^ComeAgainText|^NotYetText|FoundAnEgg|ReceivedEgg|TakeGoodCareOfEgg|ItsGoingToHatch|NoRoomForEgg"
     r"|ThereIsNoEgg|IllKeepItThanks", "System/daycare.csv"),
    (r"^Mail|Mailbox|PokemonRemoveMail|PCMonHoldingMail|MoveMonWOMail", "System/mail.csv"),
    (r"^Look|PutAway|SetUpTheDeco|WhichSidePut|AlreadySetUp|IncenseBurner|MerchandiseShelf|^TVText|^WindowText"
     r"|HomepageText", "System/decorations.csv"),
    (r"^(Slots|CardFlip)|MemoryGame|CoinCase|NoCoin|CoinsScattered|WhatDoYouWantToPlayWith", "System/game_corner.csv"),
    (r"^(NPCTrade|Link|Mobile|MysteryGift)|Text_NPCTraded|RetrieveMysteryGift|OTSends|ForYourMon|YourFriendIsNotReady"
     r"|BidsFarewell|TakeGoodCareOfMon", "System/trades_link.csv"),
    (r"^(Mart|Pokecenter(?!.*PC))|NothingToSell", "System/marts_centers.csv"),
    (r"^(PC|PlayersPC|Pokecenter.*PC)|TheBoxIsFull|ChangeBoxSave", "System/pc_storage.csv"),
    (r"Sav(e|ed|ing)|AnotherSaveFile|Window.*Error|SpaceSpaceColon|AskGiveNickname|ChangeWhichNumber|EnterThe"
     r"|(BG|Coordinates|Corrupted|Test)EventText|UnusedNothingHere|NothingToChoose", "System/save_menus.csv"),
    (r"^Ask(Cut|Surf|Waterfall|Whirlpool|Headbutt|FloorElevator)|^Use(Cut|Dig|Headbutt|SweetScent|Waterfall|Whirlpool"
     r"|EscapeRope)|CanCut|CutNothing|HeadbuttNothing|SweetScentNothing|CantSurf|AlreadySurfing|UsedSurf"
     r"|AlreadyUsingStrength|CantUseDig|CantUseTeleport|TeleportReturn|HugeWaterfall|MayPassWhirlpool|Bike"
     r"|NoCycling|FoundItem|NothingHere|BadgeRequired|Fruit|FellFromTree|^Rod|HookedPokemon|Squirtbottle"
     r"|PoisonWhiteout|WhitedOut|Warping|BellChimed", "System/field_overworld.csv"),
    (r"Learn|ForgetMove|MoveAskForget|MoveCantForgetHM|MoveForgot|MoveKnowsOne|Text_MoveForgetCount|Evolv"
     r"|KnowsMove|PrestoAllDone|CongratulationsYourPokemon", "System/learn_evolve.csv"),
    (r"^(Item|Items|Ball|Booted|PP|Repel)|Text_BallCaught|TMHM|RaiseThePP|RestoreThePP|PackNoItem|Pocket"
     r"|ThatItemCantBePut|TheItemWasPut|ReceiveItem|YouCanHaveThis|ThrewAway|AskThrowAway|AskQuantityThrowAway"
     r"|AskItemMove|CantCarryItem|CantUseItem|CantRegister|MayRegisterItem|RegisteredItem|Flute|SacredAsh"
     r"|YourMonsHPWasHealed|HPIsFull|NotEnoughHP|CuredOfPoison|BurnWasHealed|RidOfParalysis|Revitalized"
     r"|HealthReturned|CameToItsSenses|Pokemon(HoldItem|SwapItem|AskSwapItem|TookItem|NotHolding)|AnEggCantHoldAnItem"
     r"|WontRiseAnymore|WontDropAnymore|YouCantUseItInABattle|ThatCantBeUsedRightNow|ButNoSpace|ContainedMove"
     r"|Itemfinder", "System/items_bag.csv"),
    (r".", "System/battle.csv"),
]
# ROM banks holding phone calls (Gold/Silver 41; Crystal 6d, 19, 5d, 28): used
# only for lines no map reaches, since bank 19 also holds map text in Gold/Silver
G2_PHONE_BANKS = {"41", "6d", "19", "5d", "28"}
# Crystal scripts many maps run: trainers swapping numbers, the Center nurse
G2_COMMON_BANKS = {"29": "System/phone.csv", "6c": "System/marts_centers.csv"}
# standard texts many maps share, by label (checked before G2_LABELS)
G2_STD_LABELS = [
    (r"^(Nurse|PokeComNurse)|PokecenterSign|MartSign", "System/marts_centers.csv"),
    (r"^(AskNumber|NumberAccepted|NumberDeclined|Rematch)", "System/phone.csv"),
    (r"^CoinVendor", "System/game_corner.csv"),
    (r"Bookshelf|^GymStatue|^MaySmash|^ObjectEventText", "System/field_overworld.csv"),
    (r"^ReceivedItem", "System/items_bag.csv"),
]
G2_ENGINE = [
    ("online_link", r"/union/|TradeMenu|TradeAnim|UnionCenter2F"),
    ("battle", r"src/battle/|BattleState|EvolutionAnim"),
    ("pokegear", r"Pokegear|InitClock|Clock|PhoneRing|BuenaPassword|BankOfMom|MomShopping"),
    ("options", r"OptionsMenu"),
    ("minigames", r"PrizeMenu|CardFlip|SlotMachine|PhotoStudio|UnownPrinter|ContestMenu|BattleTower|battle_tower"),
    ("overworld", r"src/world/|src/script/|src/core/"),
    ("menus", r"."),
]
_G2_ADDR = r'"([0-9a-f]{2}:[0-9a-f]{4})"'


def _g2_addrs(notes):
    return re.findall(r"[0-9a-f]{2}:[0-9a-f]{4}", notes)


def _g2_label(notes):
    labs = [x for x in re.split(r"[ ,;]+", re.sub(r"\(\+\d+\)", "", notes).strip())
            if x and not re.fullmatch(r"[0-9a-f]{2}:[0-9a-f]{4}", x)]
    return labs[0].lstrip("_") if labs else ""


class _Gen2:
    """One Gen 2 game's maps, Town Map landmarks and scripts, and which maps
    reach each text address."""

    @classmethod
    def load(cls, root, versions):
        for v in versions:
            d = os.path.join(root or "", v, "data", "generated")
            if root and all(os.path.isfile(os.path.join(d, f)) for f in ("maps.lua", "scripts.lua", "landmarks.lua")):
                return cls(d)
        return None

    def __init__(self, d):
        self.lm, cur = {}, {}
        for line in open(os.path.join(d, "landmarks.lua"), encoding="utf-8"):
            m = re.match(r'\s+(index|name) = "?([^",]+)"?,', line)
            if m:
                cur[m.group(1)] = m.group(2)
                if "index" in cur and "name" in cur:
                    self.lm[int(cur["index"])] = cur["name"].replace("\\n", " ")
                    cur = {}
        self.maps, mid = {}, None
        for line in open(os.path.join(d, "maps.lua"), encoding="utf-8"):
            m = re.match(r"^  ([A-Z0-9_]+) = \{", line)
            if m:
                mid = m.group(1)
                self.maps[mid] = {"lm": None, "scripts": set(), "texts": set()}
                continue
            if mid is None:
                continue
            m = re.match(r"^    landmark = (\d+),", line)
            if m:
                self.maps[mid]["lm"] = int(m.group(1))
            m = re.match(r"^\s+(scriptKey|script|key) = " + _G2_ADDR, line)
            if m:
                self.maps[mid]["scripts"].add(m.group(2))
            m = re.match(r"^\s+(seenText|winText|lossText|text) = " + _G2_ADDR, line)
            if m:
                self.maps[mid]["texts"].add(m.group(2))
        scr, sid = {}, None
        for line in open(os.path.join(d, "scripts.lua"), encoding="utf-8"):
            m = re.match(r'^  \["([0-9a-f]{2}:[0-9a-f]{4})"\] = \{', line)
            if m:
                sid = m.group(1)
                scr[sid] = (set(), set())
                continue
            if sid is None:
                continue
            m = re.match(r"^\s+(scriptKey|script|key) = " + _G2_ADDR, line)
            if m:
                scr[sid][0].add(m.group(2))
            m = re.match(r"^\s+(winText|lossText|text|seenText) = " + _G2_ADDR, line)
            if m:
                scr[sid][1].add(m.group(2))
        self.t2m = collections.defaultdict(set)
        for name, mp in self.maps.items():
            seen, todo, texts = set(), list(mp["scripts"]), set(mp["texts"])
            while todo:
                s = todo.pop()
                if s in seen or s not in scr:
                    continue
                seen.add(s)
                refs, ts = scr[s]
                texts |= ts
                todo += list(refs)
            for t in texts:
                self.t2m[t].add(name)

    def map_file(self, name, base):
        for pat, stem in G2_MAP_OVERRIDES:
            if re.search(pat, name):
                return _stem_file(base, stem)
        place = self.lm.get(self.maps[name]["lm"], "?")
        m = re.fullmatch(r"ROUTE (\d+)", place)
        if m:
            return _stem_file(base, "R%02d" % int(m.group(1)))
        stem = G2_TOWNS.get(place) or G2_DUNGEONS.get(place)
        return _stem_file(base, stem) if stem else _f(base, "system", "other.csv")

    @staticmethod
    def label_file(lab, base, shared=False):
        """For a line many maps share (shared=True) only a specific rule counts."""
        rules = G2_STD_LABELS + (G2_LABELS[:-1] if shared else G2_LABELS)
        for pat, dest in rules:
            if re.search(pat, lab):
                return _stem_file(base, dest)
        return None

    def file(self, row, base):
        kind, notes = row[0], row[6]
        if kind == "strings":
            src = re.sub(r":\d+.*$", "", notes)
            name = next(n for n, pat in G2_ENGINE if re.search(pat, src))
            return _f(base, "engine", name + ".csv")
        if kind != "dialogue":
            return _f(base, "names", kind + ".csv")
        addrs = _g2_addrs(notes)
        ms = {m for a in addrs for m in self.t2m.get(a, ())}
        files = {self.map_file(m, base) for m in ms}
        banks = {a[:2] for a in addrs}
        lab = _g2_label(notes)
        if len(files) == 1:
            return files.pop()
        if files and lab and self.label_file(lab, base, shared=True):
            return self.label_file(lab, base, shared=True)
        if len(ms) >= 5 and banks & G2_COMMON_BANKS.keys():
            return _stem_file(base, G2_COMMON_BANKS[sorted(banks & G2_COMMON_BANKS.keys())[0]])
        if files:
            return f"{base}/shared.csv"
        if lab:
            return self.label_file(lab, base)
        if banks & G2_PHONE_BANKS:
            return _f(base, "system", "phone.csv")
        return _f(base, "system", "other.csv")


def _dex2_file(row):
    v = _versions(row)
    if row[0] == "dex_kind":
        return _f("G2/Crystal", "pokedex", "categories.csv") if v == {"crystal"} \
            else _f("G2", "pokedex", "categories.csv")
    if v == {"crystal"}:
        return _f("G2/Crystal", "pokedex", "entries.csv")
    order = [x for x in ("gold", "silver", "crystal") if x in v] + sorted(v - {"gold", "silver", "crystal"})
    return _f("G2", "pokedex", "_".join(order) + ".csv")


# =============================================================== Gen 3
# region-map section -> stem; Ruby/Sapphire/Emerald share 0-87
RSE_SECTIONS = {
    0: "T101_littleroot", 1: "T102_oldale", 2: "T103_dewford", 3: "T104_lavaridge", 4: "T105_fallarbor",
    5: "T106_verdanturf", 6: "T107_pacifidlog", 7: "C101_petalburg", 8: "C102_slateport", 9: "C103_mauville",
    10: "C104_rustboro", 11: "C105_fortree", 12: "C106_lilycove", 13: "C107_mossdeep", 14: "C108_sootopolis",
    15: "C109_ever_grande",
    **{16 + n: "R%d" % (101 + n) for n in range(34)},  # Routes 101-134
    55: "D103_granite_cave", 56: "D107_mt_chimney", 57: "D112_safari_zone", 58: "D126_battle_tower",
    59: "D101_petalburg_woods", 60: "D102_rusturf_tunnel", 61: "D109_abandoned_ship", 62: "D110_new_mauville",
    63: "D106_meteor_falls", 64: "D106_meteor_falls", 65: "D113_mt_pyre", 66: "D114_hideout",
    67: "D115_shoal_cave", 68: "D116_seafloor_cavern", 70: "D118_victory_road", 72: "D117_cave_of_origin",
    73: "D125_southern_island", 74: "D105_fiery_path", 75: "D105_fiery_path", 76: "D108_jagged_pass",
    77: "D108_jagged_pass", 78: "D120_sealed_chamber", 80: "D111_scorched_slab", 81: "D122_island_cave",
    82: "D121_desert_ruins", 83: "D123_ancient_tomb", 84: "T101_littleroot", 85: "D124_sky_pillar",
    86: "System/secret_bases.csv", 87: "T00_cable_club",
}
# Emerald: the Battle Tower's section is the Battle Frontier; its own places from 197
EMERALD_SECTIONS = {
    **RSE_SECTIONS, 58: "F00_battle_frontier",
    197: "D114_hideout", 198: "D130_magma_hideout", 199: "D128_mirage_tower", 200: "D136_birth_island",
    201: "D135_faraway_island", 202: "D132_artisan_cave", 203: "D133_marine_cave", 204: "D133_marine_cave",
    205: "D134_terra_cave", 206: "W105", 207: "W125", 208: "W129", 209: "D131_desert_underpass",
    210: "D129_altering_cave", 211: "D137_navel_rock", 212: "D127_trainer_hill",
}
# FireRed/LeafGreen: Kanto keeps Gen 1's codes; Sevii islands number by island
FRLG_SECTIONS = {
    88: "T01_pallet", 89: "T02_viridian", 90: "T03_pewter", 91: "T04_cerulean", 92: "T05_lavender",
    93: "T06_vermilion", 94: "T07_celadon", 95: "T08_fuchsia", 96: "T09_cinnabar", 97: "T10_indigo_plateau",
    98: "T11_saffron",
    **{100 + n: "R%02d" % n for n in range(1, 26)},  # Routes 1-25
    126: "D01_viridian_forest", 127: "D02_mt_moon", 128: "D03_ss_anne", 129: "D04_underground_path_ns",
    130: "D06_underground_path_we", 131: "D07_digletts_cave", 132: "D08_victory_road", 133: "D09_rocket_hideout",
    134: "D10_silph_co", 135: "D11_pokemon_mansion", 136: "D12_safari_zone", 137: "D14_pokemon_league",
    138: "D15_rock_tunnel", 139: "D16_seafoam_islands", 140: "T05_lavender", 141: "D13_cerulean_cave",
    142: "R10",
    143: "T41_one_island", 144: "T42_two_island", 145: "T43_three_island", 146: "T44_four_island",
    147: "T45_five_island", 149: "T46_six_island", 148: "T47_seven_island",
    150: "R411_kindle_road", 195: "R411_kindle_road", 151: "R412_treasure_beach", 152: "R421_cape_brink",
    153: "R431_bond_bridge", 154: "R432_three_isle_port", 159: "R451_resort_gorgeous",
    160: "R452_water_labyrinth", 161: "R453_five_isle_meadow", 162: "R454_memorial_pillar",
    163: "R461_outcast_island", 164: "R462_green_path", 165: "R463_water_path", 166: "R464_ruin_valley",
    167: "D471_trainer_tower", 168: "R472_canyon_entrance", 169: "R473_sevault_canyon", 170: "R474_tanoby_ruins",
    175: "D411_mt_ember", 176: "D431_berry_forest", 185: "D432_dunsparce_tunnel", 177: "D441_icefall_cave",
    178: "D451_rocket_warehouse", 181: "D452_lost_cave", 180: "D461_dotted_hole", 182: "D462_pattern_bush",
    183: "D463_altering_cave", 179: "D471_trainer_tower", 186: "D472_tanoby_chambers",
    **{s: "D472_tanoby_chambers" for s in range(188, 195)},
    174: "D481_navel_rock", 187: "D482_birth_island", 196: "T00_cable_club",
}
RS_MAP_OVERRIDES = [
    (r"^EverGrandeCity_(SidneysRoom|PhoebesRoom|GlaciasRoom|DrakesRoom|ChampionsRoom|HallOfFame)", "D119_pokemon_league"),
    (r"^Route121_SafariZoneEntrance", "D112_safari_zone"),
    (r"^Route110_TrickHouse", "D104_trick_house"),
    (r"^Underwater1$", "W124"), (r"^Underwater2$", "W126"), (r"^Underwater3$", "W127"), (r"^Underwater4$", "W128"),
    (r"^Underwater_SootopolisCity", "W126"), (r"^Underwater_SeafloorCavern", "D116_seafloor_cavern"),
    (r"^Underwater_(Route134|SealedChamber)", "D120_sealed_chamber"),
    (r"^SecretBase_", "System/secret_bases.csv"),
]
EMERALD_MAP_OVERRIDES = [
    (r"^BattleFrontier_BattleTower", "F01_battle_tower"), (r"^BattleFrontier_BattleDome", "F02_battle_dome"),
    (r"^BattleFrontier_BattlePalace", "F03_battle_palace"), (r"^BattleFrontier_BattleArena", "F04_battle_arena"),
    (r"^BattleFrontier_BattleFactory", "F05_battle_factory"), (r"^BattleFrontier_BattlePike", "F06_battle_pike"),
    (r"^BattleFrontier_BattlePyramid", "F07_battle_pyramid"), (r"^BattleFrontier_", "F00_battle_frontier"),
    (r"^EverGrandeCity_(SidneysRoom|PhoebesRoom|GlaciasRoom|DrakesRoom|ChampionsRoom|HallOfFame|Hall\d)",
     "D119_pokemon_league"),
    (r"^Route121_SafariZoneEntrance", "D112_safari_zone"), (r"^Route110_TrickHouse", "D104_trick_house"),
    (r"^Underwater_Route(\d+)$", None), (r"^Underwater_SootopolisCity", "W126"),
    (r"^Underwater_SeafloorCavern", "D116_seafloor_cavern"), (r"^Underwater_SealedChamber", "D120_sealed_chamber"),
    (r"^Underwater_MarineCave", "D133_marine_cave"), (r"^SecretBase_", "System/secret_bases.csv"),
]
FRLG_MAP_OVERRIDES = [(r"^FuchsiaCity_SafariZone_(Entrance|Office)", "D12_safari_zone")]
# event map constants spelled differently from the map ids
FRLG_EVENT_ALIASES = {
    "FR_OAKS_LAB": "PalletTown_ProfessorOaksLab", "FR_PLAYERS_HOUSE_1F": "PalletTown_PlayersHouse_1F",
    "FR_PLAYERS_HOUSE_2F": "PalletTown_PlayersHouse_2F", "FR_RIVALS_HOUSE": "PalletTown_RivalsHouse",
    "SEVII_ONE_ISLAND": "OneIsland", "SEVII_ONE_ISLAND_HARBOR": "OneIsland_Harbor",
    "SEVII_ONE_ISLAND_HOUSE1": "OneIsland_House1", "SEVII_ONE_ISLAND_HOUSE2": "OneIsland_House2",
    "SEVII_ONE_ISLAND_KINDLE_ROAD": "OneIsland_KindleRoad",
    "SEVII_ONE_ISLAND_POKECENTER": "OneIsland_PokemonCenter_1F",
    "SEVII_ONE_ISLAND_POKECENTER_2F": "OneIsland_PokemonCenter_2F",
    "SEVII_ONE_ISLAND_TREASURE_BEACH": "OneIsland_TreasureBeach",
}

# Ruby/Sapphire families (rows carry aliases from several decompilations: the
# first label that matches something specific wins)
RS_LABELS = [
    (r"^(PlayersHouse|RivalsHouse)_", "T101_littleroot"), (r"^SafariZone_", "D112_safari_zone"),
    (r"^AbandonedShip_", "D109_abandoned_ship"), (r"^BattleFrontier_BattleTower", "D126_battle_tower"),
    (r"^EventTicket_", "D125_southern_island"), (r"^ClosedSootopolisDoor", "C108_sootopolis"),
    (r"^DaycareText_", "R117"),
    # Mauville's rotating old man: Bard, Hipster, Trader, Storyteller, Giddy
    (r"^(gTextTrader|gTextStoryteller|gTextBard|gTextHipster|gTextGiddy|GiddyText)|HipsterPage|BardSong|BardsSong",
     "C103_mauville"),
    (r"^gBirchSpeech", "intro.csv"),
    (r"^DexDescription_", "Pokedex/descriptions.csv"),
    (r"^MoveDescription_", "Descriptions/moves.csv"), (r"^gItemDescription", "Descriptions/items.csv"),
    (r"^gAbilityDescription", "Descriptions/abilities.csv"), (r"^DecorDesc_", "Descriptions/decorations.csv"),
    (r"Ribbon", "Descriptions/ribbons.csv"),
    (r"^NatureName_", "Names/natures.csv"),
    (r"^gTrainerClassNames", "Names/trainer_classes.csv"),
    (r"^(gBirchDexRating|gDexText|DexText)", "System/pokedex.csv"),
    (r"^gTrickHouse", "D104_trick_house"),
    (r"^sCreditsText", "System/credits.csv"),
    (r"^(Battle|gBattle|STRINGID|gStatusConditionString)", "System/battle.csv"),
    (r"Contest", "System/contests.csv"),
    (r"SecretBase", "System/secret_bases.csv"),
    (r"^(Berry|sBlender|gBerry|Pokeblock)", "System/berries.csv"),
    (r"^(GabbyAndTy|BravoTrainer|SmartShopper|gTV|gPokeNews|TVShow)", "System/tv_shows.csv"),
    (r"MatchCall|TrainerEye", "System/pokenav.csv"),
    (r"^(g?OtherText|gText)_.*Mail|^Mail", "System/mail.csv"),
    (r"Combine.*Phrase|CombineTwo|Phrase|EditedText|DescribeFeelings|MakeProfile|MakeMessage|ConfirmTrendy"
     r"|EntireTextCantBeDeleted|FindWordsThatDescribe|^gEasyChat", "System/easy_chat.csv"),
    (r"^(Roulette|gUnknown_Slot|SlotMachine)", "System/game_corner.csv"),
    (r"^(CableClub|Trade|UnusedMixRecords|Link|gLink|gTradeText)", "System/trades_link.csv"),
    (r"^(Cannot|Used(Cut|Strength)|AlreadyUsedStrength|Surfacing|SweetScent|UnderwaterCannot|UseCutPrompt"
     r"|fieldPoison|Common_Text_LookCloserAtMap)", "System/field_overworld.csv"),
    (r"^(PCText|gPCText)", "System/pc_storage.csv"),
    (r"^(MartText|gText_PokemonCenter|SmartShopper)", "System/marts_centers.csv"),
    (r"^Category_", "System/credits.csv"),
    (r"^g?LandmarkName", "System/region_map.csv"),
    (r"^(SystemText|gSystemText|gMenuText|TrainerCard|gSaveFile|gError|gBoardNotInstalled|gDefault(Boy|Girl)Name"
     r"|gSelectedItem|gNonSelectedItem|gHighlightedMove)", "System/menus.csv"),
    (r"^(g?OtherText|gText)_", "System/menus.csv"),  # general interface strings
]
EMERALD_LABELS = [
    (r"BattleDome", "F02_battle_dome"), (r"BattlePalace", "F03_battle_palace"),
    (r"BattleArena|gRefereeStringsTable", "F04_battle_arena"), (r"BattleFactory", "F05_battle_factory"),
    (r"BattlePike", "F06_battle_pike"),
    (r"BattlePyramid|sBattlePyramid|sRemaining(Items|Trainers)Hint", "F07_battle_pyramid"),
    (r"BattleTower|BravoTrainerBattleTower|^sPartner(Apprentice)?Texts", "F01_battle_tower"),
    (r"BattleFrontier|Frontier", "F00_battle_frontier"),
    (r"^(PlayersHouse|RivalsHouse)_", "T101_littleroot"), (r"^SafariZone_", "D112_safari_zone"),
    (r"^AbandonedShip_", "D109_abandoned_ship"), (r"^EventTicket_", "D125_southern_island"),
    (r"^DaycareText_", "R117"), (r"^gTrickHouse", "D104_trick_house"),
    (r"^(gTextTrader|gTextStoryteller|gTextBard|gTextHipster|gTextGiddy|GiddyText)|HipsterPage|BardSong|BardsSong",
     "C103_mauville"),
    (r"^gBirchSpeech", "intro.csv"),
    (r"^DexDescription_", "Pokedex/descriptions.csv"),
    (r"^MoveDescription_", "Descriptions/moves.csv"), (r"^gItemDescription", "Descriptions/items.csv"),
    (r"^gAbilityDescription", "Descriptions/abilities.csv"), (r"^DecorDesc_", "Descriptions/decorations.csv"),
    (r"Ribbon", "Descriptions/ribbons.csv"),
    (r"^NatureName_", "Names/natures.csv"), (r"^gTrainerClassNames", "Names/trainer_classes.csv"),
    (r"^(gBirchDexRating|gDexText|DexText)", "System/pokedex.csv"),
    (r"^sCreditsText", "System/credits.csv"),
    (r"^(Battle|gBattle|STRINGID|gStatusConditionString)", "System/battle.csv"),
    (r"Contest", "System/contests.csv"), (r"SecretBase", "System/secret_bases.csv"),
    (r"^(Berry|sBlender|gBerry|Pokeblock)", "System/berries.csv"),
    (r"^(GabbyAndTy|BravoTrainer|SmartShopper|gTV|TV|gPokeNews)", "System/tv_shows.csv"),
    (r"MatchCall|TrainerEye", "System/pokenav.csv"),
    (r"^MoveTutor|NameRater|MoveDeleter|MoveRelearner", "System/services.csv"),
    (r"^(g?OtherText|gText)_.*Mail|^Mail", "System/mail.csv"),
    (r"Combine.*Phrase|CombineTwo|Phrase|EditedText|DescribeFeelings|MakeProfile|MakeMessage|ConfirmTrendy"
     r"|EntireTextCantBeDeleted|FindWordsThatDescribe|^gEasyChat", "System/easy_chat.csv"),
    (r"^(Roulette|SlotMachine)", "System/game_corner.csv"),
    (r"^(CableClub|Trade|gTrade|Link|gLink|sChooseTrainerTexts|sLinkGroupAction|sJPLinkGroupAction|Union)",
     "System/trades_link.csv"),
    (r"^(Cannot|Used(Cut|Strength)|AlreadyUsedStrength|Surfacing|SweetScent|UnderwaterCannot|UseCutPrompt"
     r"|fieldPoison|Common_Text_LookCloserAtMap)", "System/field_overworld.csv"),
    (r"^(PCText|gPCText)", "System/pc_storage.csv"),
    (r"^(MartText)", "System/marts_centers.csv"),
    (r"^Category_", "System/credits.csv"), (r"^g?LandmarkName", "System/region_map.csv"),
    (r"^(SystemText|gSystemText|gMenuText|TrainerCard|gSaveFile|gError|gBoardNotInstalled|gDefault(Boy|Girl)Name"
     r"|gStdStrings)", "System/menus.csv"),
    (r"^(g?OtherText|gText|sText)_", "System/menus.csv"),
]
FRLG_LABELS = [
    # Ruby/Sapphire berry and contest text left unused in the FRLG ROM
    (r"^(SootopolisCity|Route1\d\d|LilycoveCity|SouthernIsland|GiddyText)", "System/unused.csv"),
    (r"^(gOakSpeech|gNameChoice|gPikachuIntro)", "intro.csv"),
    (r"^Aide_", "shared.csv"),
    (r"^Help_|HelpSystem", "System/help.csv"),
    (r"FameChecker", "System/fame_checker.csv"),
    (r"^(Pokedude|gTeachyTv)", "System/teachy_tv.csv"),
    (r"^gCreditsString", "System/credits.csv"),
    (r"^(PokedexRating|gDexText|DexText)", "System/pokedex.csv"),
    (r"^gTrainerClassNames", "Names/trainer_classes.csv"),
    (r"^(Battle|gBattle|STRINGID)", "System/battle.csv"),
    (r"^(CableClub|Trade|gTrade|Link|gLink|sLinkGroupAction)", "System/trades_link.csv"),
    (r"^DayCare", "System/daycare.csv"),
    (r"^VSSeeker", "System/items_bag.csv"),
    (r"^SafariZone_", "D12_safari_zone"),
    (r"^(PCText|gPCText|Text_Accessed)", "System/pc_storage.csv"),
    (r"^(gStdStringPtrs|sCursorOptions|gControlsGuide|gStartMenuDesc|gString|gOtherText|gText|sText|Text_|gTextJP"
     r"|sJPText)", "System/menus.csv"),
]
# lines both FRLG and RSE define, by where the RSE copy would go
SHARED_PLACE_RULES = [  # RSE place-file lines: facilities and generic lines
    (r"DayCare|Daycare|^Route117_", "System/daycare.csv"),
    (r"NameRater|MoveDeleter|MoveRelearner", "System/services.csv"),
    (r"Colosseum|TradeCenter", "System/trades_link.csv"),
    (r"BardsSong|BardSong", "System/easy_chat.csv"),
    (r"^BattleTower_BattleRoom", "System/menus.csv"),
]
SHARED_LABEL_RULES = [  # Emerald copies
    (r"^gTypeNames", "Names/types.csv"),
    (r"DayCare|Daycare|^Route117_", "System/daycare.csv"),
    (r"NameRater|MoveDeleter|MoveRelearner", "System/services.csv"),
    (r"^(BattleText|gBattle|STRINGID)", "System/battle.csv"),
    (r"^(CableClub|Trade|gTrade|Link|gLink|Union)|^s(ChooseTrainer|BattleReaction|ChatReaction|PlayersNeededOrMode"
     r"|JPLinkGroupAction)Texts|Colosseum|TradeCenter", "System/trades_link.csv"),
    (r"Contest", "System/contests.csv"), (r"SecretBase", "System/secret_bases.csv"),
    (r"^(Berry|sBlender|gBerry)", "System/berries.csv"), (r"MatchCall|TrainerEye", "System/pokenav.csv"),
    (r"Mail", "System/mail.csv"), (r"Phrase|EasyChat|BardsSong", "System/easy_chat.csv"),
    (r"^(PCText|gPCText)", "System/pc_storage.csv"), (r"Dex", "System/pokedex.csv"),
    (r"^(g?OtherText|gText|sText|SystemText|gSystemText|gMenuText|sCardColorTexts)", "System/menus.csv"),
]
GAME_ORDER = ["firered", "leafgreen", "ruby", "sapphire", "emerald"]


def _g3_labels(notes):
    return [x.lstrip("_") for x in re.split(r"[ ,;]+", re.sub(r"\(\+\d+\)", "", notes).strip())
            if x and not x.startswith("g3:")]


def _g3_addrs(notes):
    return re.findall(r"g3:[0-9a-f]{8}", notes)


def _norm(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


class _Gen3:
    """One Gen 3 game's maps (map id -> region-map section) and, for lines
    without a map label, which maps reach each text address."""

    @classmethod
    def load(cls, root, versions, family):
        for v in versions:
            d = os.path.join(root or "", v, "data", "generated", "gba")
            if root and os.path.isfile(os.path.join(d, "map_tree", "census.json")):
                return cls(d, family)
        return None

    def __init__(self, d, family):
        self.family = family
        with open(os.path.join(d, "map_tree", "census.json"), encoding="utf-8") as fh:
            census = json.load(fh)
        self.sec_of = {}
        for g in census["groups"]:
            for m in g["maps"]:
                with open(os.path.join(d, "map_tree", "maps", m["slot"], "header.json"), encoding="utf-8") as fh:
                    self.sec_of[m["id"]] = json.load(fh).get("regionMapSectionId")
        self.map_ids = sorted(self.sec_of, key=len, reverse=True)
        self.t2m = None
        if family != "rs":
            self._trace(d)

    def _trace(self, d):
        """map event constant -> map id, and text address -> map ids."""
        prefix = "EM" if self.family == "emerald" else "FR"
        self.id_of_const = {prefix + _norm(mid): mid for mid in self.sec_of}
        if self.family == "frlg":
            for const, mid in FRLG_EVENT_ALIASES.items():
                self.id_of_const[_norm(const)] = mid
        scr, cur = {}, None
        path = os.path.join(d, "scripts", "scripts.lua")
        if os.path.isfile(path):
            for line in open(path, encoding="utf-8"):
                m = re.match(r'^(?:  \["(g3:[0-9a-f]{8})"\]|  ([A-Za-z_][A-Za-z0-9_]*)|T\["([^"]+)"\]) = \{', line)
                if m:
                    cur = m.group(1) or m.group(2) or m.group(3)
                    scr[cur] = set()
                    continue
                if cur:
                    scr[cur].update(re.findall(r'"(g3:[0-9a-f]{8})"', line))
        ev, cur = {}, None
        path = os.path.join(d, "scripts", "events.lua")
        if os.path.isfile(path):
            for line in open(path, encoding="utf-8"):
                m = re.match(r"^  ([A-Z0-9_]+) = \{", line) or re.match(r'^T\["([A-Z0-9_]+)"\] = \{', line)
                if m:
                    cur = m.group(1)
                    ev[cur] = set()
                    continue
                if cur:
                    ev[cur].update(re.findall(r'"(g3:[0-9a-f]{8})"', line))
        self.t2m = collections.defaultdict(set)
        for const, starts in ev.items():
            mid = self.id_of_const.get(_norm(const))
            if mid is None:
                continue
            seen, todo = set(), list(starts)
            while todo:
                a = todo.pop()
                if a in seen:
                    continue
                seen.add(a)
                self.t2m[a].add(mid)
                if a in scr:
                    todo += list(scr[a])

    def _map_label(self, lab):
        for m in self.map_ids:
            if lab == m or lab.startswith(m + "_"):
                return m
        return None

    # ---- Ruby/Sapphire ----
    def rs_map_file(self, mid, base="G3/RSE"):
        for pat, stem in RS_MAP_OVERRIDES:
            if re.search(pat, mid):
                return _stem_file(base, stem)
        stem = RSE_SECTIONS.get(self.sec_of[mid])
        return _stem_file(base, stem) if stem else _f(base, "system", "other.csv")

    def rs_file(self, row, base="G3/RSE"):
        kind, notes = row[0], row[6]
        if kind == "strings":
            src = re.sub(r":\d+.*$", "", notes)
            return _f(base, "engine", ("online_link" if re.search(r"union|cable|mystery|link", src)
                                       else "overworld") + ".csv")
        if kind != "dialogue":
            return _f(base, "names", kind + ".csv")
        for lab in _g3_labels(notes):
            mid = self._map_label(lab)
            if mid:
                return self.rs_map_file(mid, base)
            for pat, dest in RS_LABELS:
                if re.search(pat, lab):
                    return _stem_file(base, dest)
        return _f(base, "system", "other.csv")

    # ---- traced families: FireRed/LeafGreen, Emerald ----
    def _traced(self, row, base, map_file, labels):
        """A map-named first label picks the map; otherwise the maps whose
        scripts reach the text; otherwise label families."""
        labs = _g3_labels(row[6])
        if labs:
            mid = self._map_label(labs[0])
            if mid:
                return map_file(mid)
        files = {map_file(mid) for a in _g3_addrs(row[6]) for mid in (self.t2m or {}).get(a, ())}
        if len(files) == 1:
            return files.pop()
        for lab in labs:
            for pat, dest in labels:
                if re.search(pat, lab):
                    return _stem_file(base, dest)
        return f"{base}/shared.csv" if files else _f(base, "system", "other.csv")

    def frlg_map_file(self, mid, base="G3/FRLG"):
        for pat, stem in FRLG_MAP_OVERRIDES:
            if re.search(pat, mid):
                return _stem_file(base, stem)
        stem = FRLG_SECTIONS.get(self.sec_of[mid])
        return _stem_file(base, stem) if stem else _f(base, "system", "other.csv")

    def frlg_file(self, row, base="G3/FRLG"):
        if row[0] == "strings":
            return _shared_engine_file(row)
        if row[0] != "dialogue":
            return _f(base, "names", row[0] + ".csv")
        return self._traced(row, base, lambda mid: self.frlg_map_file(mid, base), FRLG_LABELS)

    def emerald_map_file(self, mid, base="G3/RSE/Emerald"):
        for pat, stem in EMERALD_MAP_OVERRIDES:
            m = re.search(pat, mid)
            if m:
                return _stem_file(base, stem or "W" + m.group(1))
        stem = EMERALD_SECTIONS.get(self.sec_of[mid])
        return _stem_file(base, stem) if stem else _f(base, "system", "other.csv")

    def emerald_file(self, row, base="G3/RSE/Emerald"):
        if row[0] == "strings":
            return self.rs_file(row, base)
        if row[0] != "dialogue":
            return _f(base, "names", row[0] + ".csv")
        return self._traced(row, base, lambda mid: self.emerald_map_file(mid, base), EMERALD_LABELS)


def _shared_engine_file(row):
    """General Gen 3 engine strings (FRLG's sheet holds them) apply to all five games."""
    src = re.sub(r":\d+.*$", "", row[6])
    for name, pat in (("online_link", r"mystery|link|union|cable|wireless|pin_entry"), ("battle", r"/battle/"),
                      ("minigames", r"slot_machine|arena"), ("menus", r"src/ui/")):
        if re.search(pat, src):
            return _f("G3/Shared", "engine", name + ".csv")
    return _f("G3/Shared", "engine", "overworld.csv")


def _frlg_fallback(row):
    return _shared_engine_file(row) if row[0] == "strings" else GENERATION_PATHS["05_firered_leafgreen"]


def _shared_g3(fr_rows, rs_rows, rs_files, em_rows):
    """Keys FRLG and RSE (Ruby/Sapphire or Emerald) both define become one row in
    G3/Shared/, in the category the RSE copy belongs to. The RSE row is kept
    (it's the one the game used when both existed), with `versions` joined and
    the FRLG notes added. Returns ([(path, row)], ids of the rows it replaced)."""
    def key(r):
        return (r[0], r[1], r[2])

    rse = {}
    for i, (r, rel) in enumerate(zip(rs_rows, rs_files)):
        rse.setdefault(key(r), (r, rel, None))
    for i, r in enumerate(em_rows):
        rse.setdefault(key(r), (r, None, i))
    # position of each RS row inside its file, as the RSE split leaves it
    pos, count = {}, collections.Counter()
    for r, rel in zip(rs_rows, rs_files):
        pos[id(r)] = count[rel]
        count[rel] += 1
    em_sheet = "G3/RSE/Emerald/07_emerald_exclusive.csv"
    out, gone = [], set()
    for fr in fr_rows:
        hit = rse.get(key(fr))
        if hit is None:
            continue
        r, rel, em_i = hit
        sub = rel[len("G3/RSE/"):] if rel else None
        if sub is not None and not sub.startswith(tuple(f"{FOLDERS[k]}/" for k in ("towns", "routes", "dungeons"))):
            dest = "G3/Shared/" + sub
        else:
            rules = SHARED_PLACE_RULES if sub is not None else SHARED_LABEL_RULES
            dest = next((d for lab in _g3_labels(r[6]) for pat, d in rules if re.search(pat, lab)), None)
            if dest is None:
                dest = f"Names/{r[0]}.csv" if r[0] in ("moves", "items") else "System/other.csv"
            dest = _stem_file("G3/Shared", dest)
        row = list(r)
        vers = set(fr[5].split()) | set(row[5].split())
        row[5] = " ".join(x for x in GAME_ORDER if x in vers) + "".join(" " + x for x in sorted(vers - set(GAME_ORDER)))
        if fr[6] and fr[6] != row[6]:
            row[6] = f"{row[6]}; FRLG: {fr[6]}" if row[6] else f"FRLG: {fr[6]}"
        order = (rel, pos[id(r)]) if rel else (em_sheet, em_i)
        out.append((dest, order, row))
        gone.add(id(fr))
        gone.add(id(r))
    out.sort(key=lambda t: (t[1], t[0]))
    return [(dest, row) for dest, _, row in out], gone


def _dex3_file(row):
    """Categories: one file per family, or Shared's when both families have them.
    Entries: per version (or per pair when two versions read the same)."""
    v = _versions(row)
    if v == {"emerald"}:
        base = "G3/RSE/Emerald"
    elif v <= {"firered", "leafgreen"}:
        base = "G3/FRLG"
    elif v <= {"ruby", "sapphire", "emerald"}:
        base = "G3/RSE"
    else:
        base = "G3/Shared"
    if row[0] == "dex_kind":
        return _f(base, "pokedex", "categories.csv")
    if base == "G3/RSE/Emerald":
        return _f(base, "pokedex", "entries.csv")
    return _f(base, "pokedex", "_".join(x for x in GAME_ORDER if x in v) + ".csv")
