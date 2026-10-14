# Design prototypes (not the site)

Design rounds for the site; the chosen design gets built into the Angular site (`frontend/src`).

Open: `cd frontend/prototypes && python3 -m http.server 4300`, then one of:

| Round 5 | Open | What is different |
|---|---|---|
| **A · Guided** | http://localhost:4300/story-guided.html | the Story you chose, plus: a table under the map, 41 countries, a "neighbours" chapter and a real judgment chapter |
| **B · Your country** | http://localhost:4300/story-yours.html | asks where you read the news (guesses from the browser language), then tells the story from that country |
| **C · Margin notes** | http://localhost:4300/story-margin.html | the story is an article in a left column; the map page stays pinned on the right and is never covered; links in the text drive it |

All three share: the masthead that the nav slides over; the plane, the map (Europe, with North America as an inset)
and, under it, **the table** (its own tinted panel with a double rule; Economic/Social centred over their columns; region
chips that also filter the plane and the map); page snapping; the timeline.

**Data.** Movements are simulated (`make_demo_data.py`), and so are the 31 countries we do not measure yet (their outlet
names are unverified candidates). The judgment examples are **real** judge outputs with the judge's reasons
(`make_judgment_examples.py` → `judgments.json`, git-ignored until you decide to publish examples; A60).

**Code.** `common.js` engine (plane, map, slider, pill, masthead) · `table.js` the table · `judgment.js` the judgment
card · `story.js` scroll-driven chapters · `paper.js` page wiring · `paper.css` the look. Earlier rounds are in
`archive/` (they run on today's engine, so they look a little different from when you judged them).

Rounds so far: 1 Broadsheet (B) over Instrument and Atlas · 2 B1's paper with B2's structure · 3 the masthead fold ·
4 Story over Front page and Dossier · 5 this one.

Test parameters: `?country=Sweden&t=100` opens a country at a month; `?home=Sweden` (Your country) skips the guess;
`?scroll=600&hold=1` freezes the pill mid-scroll; `?selftest` checks the page snapping and writes the result to
`<body data-selftest>`. Display choices are A59 and A60 in `ASSUMPTIONS.md`.
