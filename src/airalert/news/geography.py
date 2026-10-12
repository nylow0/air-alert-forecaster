"""Exact local place aliases; uncertain locations abstain rather than guessing a target."""

import csv
import re
import unicodedata
from pathlib import Path

from airalert.config import CROSSWALK_PATH, RAIONS_PATH

# Canonical raion slugs are joined to the actual local universe, never synthesized.
CITY_ALIASES = {
    "kyiv-city/kyiv-city": "київ києва києві киев киева киеве kyiv kiev",
    "vinnytska/vinnytskyi": "вінниця вінниці вінницю винница виннице винницу vinnytsia",
    "vinnytska/haisynskyi": "гайсин гайсина гайсині гайдысин haisyn",
    "odeska/odeskyi": "одеса одеси одесу одесі одесса одессы одессу одессе odesa odessa "
    "чорноморськ чорноморська черноморск фонтанка пересип пересыпь",
    "kharkivska/kharkivskyi": "харків харкова харкові харківський "
    "харьков харькова харькове kharkiv",
    "dnipropetrovska/dniprovskyi": "дніпро дніпра дніпрі днепр днепра днепре dnipro",
    "lvivska/lvivskyi": "львів львова львові львов львове lviv",
    "zaporizka/zaporizkyi": "запоріжжя запоріжжі запорожье запорожья zaporizhzhia",
    "mykolaivska/mykolaivskyi": "миколаїв миколаєва миколаєві николаев николаева mykolaiv",
    "khersonska/khersonskyi": "херсон херсона херсоні херсоне kherson",
    "sumska/sumskyi": "суми сум сумы сумах sumy",
    "poltavska/poltavskyi": "полтава полтави полтаві полтаве poltava",
    "chernihivska/chernihivskyi": "чернігів чернігова чернигов чернигова chernihiv",
    "cherkaska/cherkaskyi": "черкаси черкасах черкассы cherkasy",
    "zhytomyrska/zhytomyrskyi": "житомир житомира житомирі zhytomyr",
    "kyivska/vyshhorodskyi": "вишгород вишгорода вышгород іванків иванков vyshhorod",
    "kyivska/buchanskyi": "буча бучі буче ірпінь ирпень bucha irpin",
    "kyivska/brovarskyi": "бровари бровары броварах brovary",
    "kyivska/boryspilskyi": "бориспіль борисполя борисполь boryspil",
    "dnipropetrovska/kryvorizkyi": "кривий-ріг кривой-рог kryvyi-rih",
}
OBLAST_ALIASES = {
    "cherkaska": ("черкаська", "черкасская"),
    "chernihivska": ("чернігівська", "черниговская"),
    "chernivetska": ("чернівецька", "черновицкая"),
    "dnipropetrovska": ("дніпропетровська", "днепропетровская"),
    "donetska": ("донецька", "донецкая"),
    "ivano-frankivska": ("івано-франківська", "ивано-франковская"),
    "kharkivska": ("харківська", "харьковская"),
    "khersonska": ("херсонська", "херсонская"),
    "khmelnytska": ("хмельницька", "хмельницкая"),
    "kirovohradska": ("кіровоградська", "кировоградская"),
    "kyivska": ("київська", "киевская"),
    "lvivska": ("львівська", "львовская"),
    "mykolaivska": ("миколаївська", "николаевская"),
    "odeska": ("одеська", "одесская"),
    "poltavska": ("полтавська", "полтавская"),
    "rivnenska": ("рівненська", "ровенская"),
    "sumska": ("сумська", "сумская"),
    "ternopilska": ("тернопільська", "тернопольская"),
    "vinnytska": ("вінницька", "винницкая"),
    "volynska": ("волинська", "волынская"),
    "zakarpatska": ("закарпатська", "закарпатская"),
    "zaporizka": ("запорізька", "запорожская"),
    "zhytomyrska": ("житомирська", "житомирская"),
}

# Origins may be outside the forecast universe; they never become target raions.
ORIGIN_ALIASES = (
    "Оленья",
    "Оленьї",
    "Оленьей",
    "Енгельс",
    "Энгельс",
    "Енгельса",
    "Энгельса",
    "Брянськ",
    "Брянська",
    "Брянск",
    "Брянска",
    "Курськ",
    "Курську",
    "Курск",
    "Курска",
    "Крим",
    "Криму",
    "Крым",
    "Крыма",
    "Чорне море",
    "Черное море",
    "море",
    "моря",
)
HEADING_ALIASES = (
    "north",
    "south",
    "east",
    "west",
    "northeast",
    "northwest",
    "southeast",
    "southwest",
    "північ",
    "південь",
    "схід",
    "захід",
    "північний",
    "південний",
    "східний",
    "західний",
    "север",
    "юг",
    "восток",
    "запад",
    "северный",
    "южный",
    "восточный",
    "западный",
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[^\w'-]+", " ", text).strip()


class Geography:
    def __init__(self, raions: Path = RAIONS_PATH, crosswalk: Path = CROSSWALK_PATH) -> None:
        with raions.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        with crosswalk.open(encoding="utf-8", newline="") as stream:
            shapes = list(csv.DictReader(stream))
        mapped = {r["raion_id"] for r in shapes if r["raion_id"]}
        self.raions = {r["raion_id"]: r["oblast_id"] for r in rows if r["raion_id"] in mapped}
        self.aliases: dict[str, set[str]] = {}
        for row in rows:
            rid = row["raion_id"]
            if rid not in self.raions:
                continue
            self.add(row["raion_name"], {rid})
            self.add(rid, {rid})
            targets = {r for r, o in self.raions.items() if o == row["oblast_id"]}
            self.add(row["oblast_name"], targets)
        for rid, aliases in CITY_ALIASES.items():
            if rid in self.raions:
                for alias in aliases.split():
                    self.add(alias, {rid})
        for oid, adjectives in OBLAST_ALIASES.items():
            targets = {r for r, o in self.raions.items() if o == oid}
            if not targets:
                continue
            self.add(oid, targets)
            for adjective in adjectives:
                for suffix in ("область", "обл", "області", "области", "областю"):
                    self.add(f"{adjective} {suffix}", targets)
                self.add(adjective[:-1] + "ій області", targets)
                self.add(adjective[:-2] + "ой области", targets)
                self.add(adjective[:-1] + "у область", targets)

    def add(self, alias: str, targets: set[str]) -> None:
        key = normalize(alias)
        if key in self.aliases and self.aliases[key] != targets:
            self.aliases[key] = set()  # Ambiguous aliases remain abstentions.
        else:
            self.aliases[key] = targets

    def resolve(self, mentions: list[str]) -> tuple[list[str], list[str]]:
        targets: set[str] = set()
        for mention in mentions:
            match = self.aliases.get(normalize(mention))
            if not match:
                # One unsupported/ambiguous destination invalidates the event's target mapping.
                return [], []
            targets.update(match)
        return sorted({self.raions[r] for r in targets}), sorted(targets)

    def mention_in_text(self, mention: str, text: str) -> bool:
        return f" {normalize(mention)} " in f" {normalize(text)} "

    def metadata(self, value: str | None, texts: list[str], origin: bool) -> str | None:
        """Abstain on unsupported or invented origin/heading text too."""
        if value is None:
            return None
        allowed = {normalize(v) for v in (ORIGIN_ALIASES if origin else HEADING_ALIASES)}
        allowed.update(k for k, targets in self.aliases.items() if targets)
        if normalize(value) not in allowed or not any(
            self.mention_in_text(value, t) for t in texts
        ):
            return None
        return value
