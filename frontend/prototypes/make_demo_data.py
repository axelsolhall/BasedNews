"""Demo data for the frontend prototypes: SIMULATED monthly positions, 2016-01 … 2026-10, for the real outlet list.

    PYTHONPATH=. uv run python frontend/prototypes/make_demo_data.py   → frontend/prototypes/demo-trends.json

Positions over time are not measured yet (the trend model is the MUST in BACKLOG). These smooth simulated paths only exist so
the page designs can be judged with moving dots; every prototype labels them as demo data.
"""
import json
from pathlib import Path

import numpy as np

from pipeline.sources.registry import load_outlets

# Europe + North America (design work for the "many more countries" request, 2026-10-13). The ten measured countries
# use the real outlet registry; the others use CANDIDATE outlet names (largest national news brands from memory,
# unverified, not checked against DNR) — demo labels only, never a source list.
REGIONS = ["Nordics", "Western Europe", "Southern Europe", "Central Europe & Baltics", "Eastern Europe & Balkans",
           "North America"]
COUNTRIES = [  # name, ISO 3166-1 numeric (world-atlas ids), region index
    ("Norway", "578", 0), ("Sweden", "752", 0), ("Denmark", "208", 0), ("Finland", "246", 0), ("Iceland", "352", 0),
    ("United Kingdom", "826", 1), ("Ireland", "372", 1), ("France", "250", 1), ("Belgium", "056", 1),
    ("Netherlands", "528", 1), ("Luxembourg", "442", 1), ("Germany", "276", 1), ("Austria", "040", 1),
    ("Switzerland", "756", 1),
    ("Spain", "724", 2), ("Portugal", "620", 2), ("Italy", "380", 2), ("Malta", "470", 2), ("Greece", "300", 2),
    ("Cyprus", "196", 2),
    ("Poland", "616", 3), ("Czechia", "203", 3), ("Slovakia", "703", 3), ("Hungary", "348", 3), ("Slovenia", "705", 3),
    ("Croatia", "191", 3), ("Estonia", "233", 3), ("Latvia", "428", 3), ("Lithuania", "440", 3),
    ("Romania", "642", 4), ("Bulgaria", "100", 4), ("Ukraine", "804", 4), ("Moldova", "498", 4), ("Serbia", "688", 4),
    ("Bosnia and Herzegovina", "070", 4), ("Montenegro", "499", 4), ("North Macedonia", "807", 4), ("Albania", "008", 4),
    ("United States", "840", 5), ("Canada", "124", 5), ("Mexico", "484", 5),
]
CANDIDATES = {  # five reference candidates, then extras
    "Iceland": ["RÚV", "Morgunblaðið", "Vísir", "DV", "Heimildin"],
    "Ireland": ["RTÉ", "The Irish Times", "Irish Independent", "TheJournal.ie", "Irish Examiner"],
    "Belgium": ["VRT NWS", "RTBF", "Het Laatste Nieuws", "Le Soir", "De Standaard"],
    "Netherlands": ["NOS", "De Telegraaf", "AD", "de Volkskrant", "NRC", "GeenStijl"],
    "Luxembourg": ["RTL Lëtzebuerg", "Luxemburger Wort", "L'essentiel", "Tageblatt", "Luxembourg Times"],
    "Austria": ["ORF", "Kronen Zeitung", "Der Standard", "Die Presse", "Kurier", "oe24"],
    "Switzerland": ["SRF", "20 Minuten", "Blick", "NZZ", "RTS"],
    "Portugal": ["RTP", "Público", "Expresso", "Correio da Manhã", "SIC Notícias"],
    "Malta": ["Times of Malta", "MaltaToday", "The Malta Independent", "TVM", "Newsbook"],
    "Greece": ["ERT", "Kathimerini", "Proto Thema", "Skai", "in.gr"],
    "Cyprus": ["CyBC", "Phileleftheros", "Politis", "Cyprus Mail", "Sigmalive"],
    "Poland": ["TVN24", "Onet", "Wirtualna Polska", "Gazeta Wyborcza", "Rzeczpospolita", "TV Republika"],
    "Czechia": ["ČT24", "iDNES.cz", "Novinky.cz", "Seznam Zprávy", "Hospodářské noviny"],
    "Slovakia": ["STVR", "Aktuality.sk", "SME", "Denník N", "Pravda"],
    "Hungary": ["Index", "Telex", "444", "Magyar Nemzet", "HVG", "Origo"],
    "Slovenia": ["RTV SLO", "24ur", "Delo", "Dnevnik", "N1 Slovenija"],
    "Croatia": ["HRT", "Index.hr", "Jutarnji list", "Večernji list", "24sata"],
    "Estonia": ["ERR", "Postimees", "Delfi Estonia", "Eesti Päevaleht", "Õhtuleht"],
    "Latvia": ["LSM", "Delfi Latvia", "TVNET", "Diena", "Neatkarīgā"],
    "Lithuania": ["LRT", "15min", "Delfi Lithuania", "Lrytas", "Verslo žinios"],
    "Romania": ["Digi24", "Adevărul", "HotNews", "Libertatea", "G4Media"],
    "Bulgaria": ["BNT", "Dnevnik", "Mediapool", "24 Chasa", "bTV Novinite"],
    "Ukraine": ["Suspilne", "Ukrainska Pravda", "NV", "Ukrinform", "RBC-Ukraine"],
    "Moldova": ["Moldova 1", "Ziarul de Gardă", "NewsMaker", "Unimedia", "Jurnal TV"],
    "Serbia": ["RTS", "Blic", "N1 Srbija", "Danas", "Politika", "Informer"],
    "Bosnia and Herzegovina": ["Klix.ba", "BHRT", "Oslobođenje", "Avaz", "N1 BiH"],
    "Montenegro": ["RTCG", "Vijesti", "Pobjeda", "CdM", "Dan"],
    "North Macedonia": ["MRT", "Sloboden pečat", "Nova Makedonija", "Kanal 5", "Telma"],
    "Albania": ["RTSH", "Top Channel", "Panorama", "Balkan Web", "Euronews Albania"],
    "Canada": ["CBC News", "CTV News", "Global News", "The Globe and Mail", "Toronto Star", "National Post", "Radio-Canada"],
    "Mexico": ["El Universal", "Milenio", "Reforma", "La Jornada", "N+", "Proceso"],
}


def outlet_list():
    """(id, name, country, tier) for every country: the registry where we measure, candidates elsewhere."""
    measured = {}
    for o in load_outlets():
        measured.setdefault(o.country, []).append((o.id, o.name, o.country, o.tier))
    out = []
    for name, _, _ in COUNTRIES:
        if name in measured:
            out += measured[name]
        else:
            out += [(f"demo-{name}-{k}".lower().replace(" ", "-"), n, name, "reference" if k < 5 else "extra")
                    for k, n in enumerate(CANDIDATES[name])]
    return out


def months(start=(2016, 1), end=(2026, 10)):
    y, m = start
    while (y, m) <= end:
        yield f"{y}-{m:02d}"
        y, m = (y + (m == 12), m % 12 + 1)


def drift(rng, n, scale=0.0035, keep=0.93, pull=0.003):
    """Smooth wandering: the velocity has memory and a weak pull back home, so paths curve instead of jitter."""
    x, v = np.zeros((n, 2)), rng.normal(0, 2 * scale, 2)
    for k in range(1, n):
        v = keep * v + rng.normal(0, scale, 2) - pull * x[k - 1]
        x[k] = x[k - 1] + v
    return x


def main():
    rng = np.random.default_rng(7)
    ms = list(months())
    n = len(ms)
    region_base = [rng.normal(0, 0.3, 2) for _ in REGIONS]
    country_base = {c: region_base[r] + rng.normal(0, 0.3, 2) for c, _, r in COUNTRIES}
    measured = {o.country for o in load_outlets()}
    out = []
    for oid, oname, country, tier in outlet_list():
        base = country_base[country] + rng.normal(0, 0.45, 2)
        path = base + drift(rng, n) + rng.normal(0, 0.012, (n, 2))   # slow drift + a little month-to-month noise
        if rng.random() < 0.25:                                    # a few outlets shift once (new owner, new editor)
            t0 = rng.integers(20, n - 15)
            path += rng.normal(0, 0.22, 2) / (1 + np.exp(-(np.arange(n)[:, None] - t0) / 2.5))
        # uncertainty: wide in the sparse archive years, narrowing once live measurement starts (≈ month 118)
        sd = 0.18 + 0.32 * np.exp(-np.arange(n) / 70) + rng.uniform(0, 0.05)
        sd = np.where(np.arange(n) >= n - 12, sd * 0.7, sd)
        s = [[round(float(x), 3), round(float(y), 3), round(float(sd[k] * rng.uniform(0.85, 1.15)), 3),
              round(float(sd[k] * rng.uniform(0.85, 1.15)), 3)] for k, (x, y) in enumerate(path)]
        out.append({"id": oid, "name": oname, "country": country, "tier": tier, "s": s})
    data = {"meta": {"demo": True, "note": "SIMULATED movements for design work, not measurements"},
            "months": ms, "regions": REGIONS,
            "countries": [{"id": c, "name": c, "iso": i, "region": r, "measured": c in measured} for c, i, r in COUNTRIES],
            "outlets": out}
    p = Path(__file__).with_name("demo-trends.json")
    p.write_text(json.dumps(data, separators=(",", ":")))
    print(f"wrote {p}: {len(out)} outlets × {n} months, {p.stat().st_size / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
