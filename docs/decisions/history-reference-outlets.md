# History reference outlets: 5 per country, 41 countries (draft for review)

Decided 2026-10-14: the history (daily, from 2010-01-01, run locally) covers the five reference outlets of each
country. Data: `data/history_outlets.json`. Sources: **registry** = today's live reference tier (DNR 2026, A29);
**DNR** = Reuters Digital News Report 2026, "online, used last week" (`pipeline/sources/dnr.py`, raw CSVs in
`data/dnr2026/`), with A29's rules (no categories, no foreign brands, no aggregators); **provisional** = the market is
not in DNR 2026 — largest national brands from memory, **to confirm**.

Choices to confirm:

- Two-language markets are split by population: Belgium 3 Flemish + 2 French-speaking, Switzerland 3 German + 2
  French, Canada 4 English + 1 French.
- Bulgaria: ABV News (27 %) left out as an aggregator → BNT. Serbia: "Investigative Reporting Network" (20 %) left
  out (brand unclear) → Danas.
- Several reference brands are younger than 2010 (e.g. Telex 2020, Seznam Zprávy 2016, CNN Portugal 2021, CNN Prima
  News 2020, N+ 2021, Nova.rs 2019, N1 2014): their history starts when they did, or at a predecessor's address.

| Country | Reference outlets | Source |
|---|---|---|
| Norway | NRK · VG · Dagbladet · TV 2 · Nettavisen | registry |
| Sweden | SVT · Aftonbladet · Dagens Nyheter · Expressen · TV4 Nyheterna | registry |
| Denmark | DR · TV 2 Denmark · Politiken · Ekstra Bladet · B.T. | registry |
| Finland | Yle · Helsingin Sanomat · Ilta-Sanomat · Iltalehti · MTV Uutiset | registry |
| United States | The New York Times · Fox News · Politico · ABC News · NBC News | registry |
| United Kingdom | BBC News · The Guardian · Daily Mail · Sky News · GB News | registry |
| Germany | tagesschau · Der Spiegel · t-online · n-tv · Bild | registry |
| France | Le Monde · 20 Minutes · TF1 Info · franceinfo · BFMTV | registry |
| Spain | El País · El Mundo · elDiario.es · 20minutos · OKDiario | registry |
| Italy | la Repubblica · ANSA · Il Fatto Quotidiano · TgCom24 · RaiNews | registry |
| Netherlands | NU.nl · NOS · AD · RTL Nieuws · De Telegraaf | DNR |
| Belgium | Het Laatste Nieuws · VRT NWS · Het Nieuwsblad · RTL Info · RTBF Info | DNR |
| Ireland | RTÉ News · TheJournal.ie · Irish Independent · BreakingNews.ie · The Irish Times | DNR |
| Austria | ORF · Kronen Zeitung · Der Standard · Heute · oe24 | DNR |
| Switzerland | 20 Minuten · SRF News · Blick · 20 minutes · RTS Info | DNR |
| Portugal | SIC Notícias · Notícias ao Minuto · Correio da Manhã · CNN Portugal · Jornal de Notícias | DNR |
| Greece | Newsbomb · in.gr · Proto Thema · News247 · Kathimerini | DNR |
| Poland | Onet · Wirtualna Polska · TVN24 · Interia · RMF24 | DNR |
| Czechia | Seznam Zprávy · iDNES.cz · Novinky.cz · Aktuálně.cz · CNN Prima News | DNR |
| Slovakia | Aktuality.sk · Topky.sk · TV Noviny · SME · Refresher | DNR |
| Hungary | Telex · 24.hu · 444 · Index · RTL | DNR |
| Croatia | Index.hr · 24sata · Dnevnik.hr · Jutarnji list · Net.hr | DNR |
| Romania | Știrile Pro TV · Digi24 · Libertatea · Știri pe surse · Adevărul | DNR |
| Bulgaria | Nova · bTV Novinite · Novini.bg · 24 Chasa · BNT | DNR |
| Serbia | N1 · Blic · Telegraf · Nova · Danas | DNR |
| Canada | CBC News · CTV News · Global News · The Globe and Mail · TVA Nouvelles | DNR |
| Mexico | Milenio · El Universal · UnoTV · N+ · Azteca Noticias | DNR |
| Iceland | RÚV · Morgunblaðið · Vísir · DV · Heimildin | provisional |
| Luxembourg | RTL Lëtzebuerg · Luxemburger Wort · L'essentiel · Tageblatt · Luxembourg Times | provisional |
| Malta | Times of Malta · MaltaToday · The Malta Independent · TVM · Newsbook | provisional |
| Cyprus | CyBC · Phileleftheros · Politis · Cyprus Mail · Sigmalive | provisional |
| Slovenia | RTV SLO · 24ur · Delo · Dnevnik · N1 Slovenija | provisional |
| Estonia | ERR · Postimees · Delfi Estonia · Eesti Päevaleht · Õhtuleht | provisional |
| Latvia | LSM · Delfi Latvia · TVNET · Diena · Neatkarīgā | provisional |
| Lithuania | LRT · 15min · Delfi Lithuania · Lrytas · Verslo žinios | provisional |
| Ukraine | Suspilne · Ukrainska Pravda · NV · Ukrinform · RBC-Ukraine | provisional |
| Moldova | Moldova 1 · Ziarul de Gardă · NewsMaker · Unimedia · Jurnal TV | provisional |
| Bosnia and Herzegovina | Klix.ba · BHRT · Oslobođenje · Avaz · N1 BiH | provisional |
| Montenegro | RTCG · Vijesti · Pobjeda · CdM · Dan | provisional |
| North Macedonia | MRT · Sloboden pečat · Nova Makedonija · Kanal 5 · Telma | provisional |
| Albania | RTSH · Top Channel · Panorama · Balkanweb · Euronews Albania | provisional |
