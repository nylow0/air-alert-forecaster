"""Exact handles, titles and audience counts verified by R4 on 2026-10-12."""

from airalert.schemas import NewsChannel

VERIFIED = (
    ("truexanewsua", "Труха⚡️Україна", 3147096),
    ("vanek_nikolaev", "Николаевский Ванёк", 2605587),
    ("insiderUKR", "INSIDER UA | Україна | Новини", 2189775),
    ("lachentyt", "Лачен пише", 1591448),
    ("kievreal1", "Реальний Київ | Украина", 1560042),
    ("voynareal", "Реальна Війна | Україна | Новини", 1511612),
    ("ukraina_novosti", "Україна 24/7 - новини", 1373007),
    ("kievinfo_kyiv", "Київ ІНФО | Новини Україна🇺🇦", 1250167),
    ("odessa_infonews", "Одесса INFO⚓️🇺🇦Новости | Одеса", 991068),
    ("war_monitor", "monitor", 968491),
    ("kpszsu", "Повітряні Сили ЗС України / Air Force of the Armed Forces of Ukraine", 908899),
    ("dnipro_alerts", "Дніпро Alerts🚀🚨", 810055),
    (
        "uniannet",
        "УНИАН - новости Украины | война с Россией | новини України | війна з Росією | УНІАН",
        703767,
    ),
    ("kharkivlife", "Харьков life | Харків🇺🇦", 666916),
    ("lvivych_news", "Львівич | Новини", 436292),
    ("UkraineNow", "Ukraine NOW", 408999),
    ("operativnoZSU", "Оперативний ЗСУ", 363574),
    ("suspilnenews", "СУСПІЛЬНЕ НОВИНИ", 262449),
    ("monitorwarr", "monitoring", 85759),
)


def defaults() -> list[NewsChannel]:
    return [NewsChannel(handle=h, title=t, subscribers=s, enabled=True) for h, t, s in VERIFIED]
