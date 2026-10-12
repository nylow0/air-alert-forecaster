"""Reviewed origin UIDs, matched to the fixed CSV universe, not transliterated guesses.

R3 checked the aligned English/Ukrainian CSV and official hierarchy. UID 92 is
Шептицький, formerly Червоноградський; 43 is Самарівський, formerly Новомосковський;
60 is Звягельський, formerly Новоград-Волинський. Community parents use the tree.
"""

from dataclasses import dataclass

import pandas as pd

from airalert.sources import array, mapping

# Each line lists official district UIDs followed by their canonical CSV suffix.
_DISTRICTS = {
    "kirovohradska": "81:kropyvnytskyi 80:oleksandriiskyi 83:novoukrainskyi 82:holovanivskyi",
    "poltavska": "107:kremenchutskyi 106:lubenskyi 109:poltavskyi 108:myrhorodskyi",
    "cherkaska": "150:zvenyhorodskyi 152:cherkaskyi 153:zolotoniskyi 151:umanskyi",
    "donetska": "55:volnovaskyi 53:donetskyi 52:mariupolskyi 50:kramatorskyi "
    "49:kalmiuskyi 51:horlivskyi 54:bakhmutskyi 56:pokrovskyi",
    "zakarpatska": "66:uzhhorodskyi 63:rakhivskyi 61:berehivskyi 62:khustskyi "
    "64:tiachivskyi 65:mukachivskyi",
    "mykolaivska": "96:bashtanskyi 97:pervomaiskyi 98:mykolaivskyi 95:voznesenskyi",
    "sumska": "118:okhtyrskyi 115:shostkynskyi 116:romenskyi 114:sumskyi 117:konotopskyi",
    "volynska": "39:lutskyi 38:volodymyr-volynskyi 40:kovelskyi 41:kamin-kashyrskyi",
    "khersonska": "130:skadovskyi 133:henicheskyi 129:beryslavskyi 132:khersonskyi 131:kakhovskyi",
    "odeska": "103:rozdilnianskyi 105:bolhradskyi 104:odeskyi 101:izmailskyi "
    "99:podilskyi 102:bilhorod-dnistrovskyi 100:berezivskyi",
    "ivano-frankivska": "68:ivano-frankivskyi 70:kolomyiskyi 69:kosivskyi "
    "72:nadvirnianskyi 71:kaluskyi 67:verkhovynskyi",
    "khmelnytska": "135:kamianets-podilskyi 136:shepetivskyi 134:khmelnytskyi",
    "vinnytska": "36:vinnytskyi 37:haisynskyi 33:mohyliv-podilskyi 32:tulchynskyi "
    "34:khmilnytskyi 35:zhmerynskyi",
    "chernihivska": "143:prylutskyi 140:chernihivskyi 141:novhorod-siverskyi "
    "142:nizhynskyi 144:koriukivskyi",
    "kyivska": "78:boryspilskyi 73:bilotserkivskyi 79:brovarskyi 77:fastivskyi "
    "76:obukhivskyi 75:buchanskyi 74:vyshhorodskyi",
    "rivnenska": "110:varaskyi 111:dubenskyi 113:sarnenskyi 112:rivnenskyi",
    "chernivetska": "139:dnistrovskyi 138:vyzhnytskyi 137:chernivetskyi",
    "ternopilska": "121:chortkivskyi 120:kremenetskyi 119:ternopilskyi",
    "lvivska": "90:lvivskyi 93:yavorivskyi 88:sambirskyi 89:stryiskyi "
    "94:zolochivskyi 91:drohobytskyi 92:chervonohradskyi",
    "dnipropetrovska": "43:samarivskyi 48:synelnykivskyi 44:dniprovskyi 45:pavlohradskyi "
    "47:nikopolskyi 42:kamianskyi 46:kryvorizkyi",
    "zhytomyrska": "58:korostenskyi 59:zhytomyrskyi 60:zviahelskyi 57:berdychivskyi",
    "kharkivska": "124:kharkivskyi 126:bohodukhivskyi 122:chuhuivskyi 125:iziumskyi "
    "128:lozivskyi 127:krasnohradskyi 123:kupianskyi",
    "zaporizka": "146:vasylivskyi 149:zaporizkyi 148:melitopolskyi 145:polohivskyi 147:berdianskyi",
}
DISTRICT_IDS = {
    uid: f"{oblast}/{suffix}"
    for oblast, entries in _DISTRICTS.items()
    for entry in entries.split()
    for uid, suffix in [entry.split(":")]
}
OBLAST_IDS = {
    "15": "kirovohradska", "19": "poltavska", "9999": "autonomous-republic-of-crimea",
    "24": "cherkaska", "28": "donetska", "31": "kyiv-city", "11": "zakarpatska",
    "17": "mykolaivska", "20": "sumska", "8": "volynska", "23": "khersonska", "18": "odeska",
    "13": "ivano-frankivska", "3": "khmelnytska", "4": "vinnytska", "25": "chernihivska",
    "14": "kyivska", "5": "rivnenska", "26": "chernivetska", "21": "ternopilska",
    "16": "luhanska", "27": "lvivska", "9": "dnipropetrovska", "10": "zhytomyrska",
    "22": "kharkivska", "12": "zaporizka",
}
NAME_ALIASES = {
    "Шептицький район": "92", "Червоноградський район": "92",
    "Самарівський район": "43", "Новомосковський район": "43",
    "Звягельський район": "60", "Новоград-Волинський район": "60",
    "Берестинський район": "127", "Красноградський район": "127",
    "Володимирський район": "38", "Володимир-Волинський район": "38",
    "м. Севастополь": "30",
}


@dataclass(frozen=True)
class Location:
    uid: str
    name: str
    oblast_id: str
    raion_ids: tuple[str, ...]
    level: str


class Locations:
    """Hierarchy-checked lookup with explicit exceptions for cities and AIU Crimea."""

    def __init__(self, tree: object, raions: pd.DataFrame) -> None:
        self.by_uid: dict[str, Location] = {}
        self.by_name: dict[str, Location] = {}
        expected = set(raions.raion_id)
        reviewed = set(DISTRICT_IDS.values()) | {"kyiv-city/kyiv-city"}
        if expected != reviewed:
            raise ValueError("Forecast universe differs from the reviewed 119-unit alias table")

        def walk(value: object, oblast: str, parent_ids: tuple[str, ...]) -> None:
            node = mapping(value)
            uid, name, kind = str(node["regionId"]), str(node["regionName"]), node["regionType"]
            if uid in OBLAST_IDS:
                oblast = OBLAST_IDS[uid]
                parent_ids = tuple(raions.loc[raions.oblast_id.eq(oblast), "raion_id"])
            elif uid in DISTRICT_IDS:
                parent_ids = (DISTRICT_IDS[uid],)
            elif uid == "1293":
                oblast, parent_ids = "kharkivska", (DISTRICT_IDS["124"],)
            elif uid == "564":
                oblast, parent_ids = "zaporizka", (DISTRICT_IDS["149"],)
            elif kind == "District" and parent_ids:
                raise ValueError(f"Unreviewed forecast district UID {uid}")
            if not oblast and uid != "0":
                raise ValueError(f"Unreviewed top-level source UID {uid}")
            level = "oblast" if kind == "State" and uid in OBLAST_IDS else (
                "raion" if kind == "District" else "hromada"
            )
            location = Location(uid, name, oblast, parent_ids, level)
            self.by_uid[uid] = location
            self.by_name[name] = location
            for child in array(node.get("regionChildIds", [])):
                walk(child, oblast, parent_ids)

        for node in array(mapping(tree)["states"]):
            walk(node, "", ())
        missing = (set(DISTRICT_IDS) | {"31"}) - set(self.by_uid)
        if missing:
            raise ValueError(f"Official hierarchy lacks reviewed forecast UIDs: {sorted(missing)}")
        self.by_uid["29"] = self.by_uid["9999"]
        self.by_uid["30"] = Location("30", "м. Севастополь", "sevastopol-city", (), "oblast")
        for name, uid in NAME_ALIASES.items():
            self.by_name[name] = self.by_uid[uid]
        mapped = {rid for loc in self.by_uid.values() for rid in loc.raion_ids}
        if mapped != expected:
            raise ValueError("Official hierarchy does not cover the reviewed universe")

    def resolve(self, uid: object = None, name: str = "") -> Location:
        if uid is not None and str(uid) in self.by_uid:
            return self.by_uid[str(uid)]
        if name in self.by_name:
            return self.by_name[name]
        raise ValueError(f"Unmapped source location UID {uid}, name {name!r}")
