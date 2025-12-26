# BasedNews

GroundNews-style news intelligence project. Ingest global news, cluster related stories, surface bias and geographic coverage, and present it in an intuitive, navigable interface.

## Goals
- Ingest news from many countries and sources.
- Map articles to shared events/stories.
- Analyze and display bias and geographic coverage.
- Provide a clean UI to explore events, sources, and bias changes.

## Core Idea
Backend data science:
- Collect news from many outlets and countries.
- Match articles about the same event across outlets.
- Use LLM pairwise comparisons to position outlets on a left/right (liberal/conservative) axis.
- Prioritize comparisons that reduce uncertainty (information gain) to minimize LLM calls.
- Assign each outlet an (x, y) position on a political plane.

Frontend product:
- Landing page centers on a world map.
- The map highlights one event covered by enough countries to be meaningful.
- Each country is colored by the mean bias score of outlets that covered that event.
- A deeper navigation area supports search, filtering, and bias exploration.
- A developer panel shows ingestion health and matching diagnostics.

Developer panel (priority):
- Ingestion: per-country per-outlet article counts by day (last 7 days).
- Matching: show which events are matched across outlets and countries.

## MVP Scope (Scandinavia)
- Countries: Norway, Sweden, Denmark.
- Sources: top 5 outlets per country (curated list stored in `data/outlets.json`).
- Language: multilingual ingestion; normalize to a shared representation so events are comparable across languages.

## Stack Options
- Java + Spring Boot for backend, TypeScript + Angular for frontend.
- Python for ML/AI tasks (clustering, bias scoring, NLP pipelines).

## Roadmap
- [ ] 1) Data foundation
  - [ ] Finalize outlet list in `data/outlets.json`.
  - [ ] Ingest RSS feeds with full-text extraction.
  - [ ] Persist dedupe cache and run scheduler.
  - [ ] Method: Python ingestion + SQLite cache.
- [ ] 2) Event matching
  - [ ] Define event similarity criteria and evaluation set.
  - [ ] Build multilingual embeddings pipeline.
  - [ ] Cluster articles into events and store event graph.
  - [ ] Method: Python (sentence transformers) + FAISS; batch jobs.
- [ ] 3) Bias scoring (LLM-assisted)
  - [ ] Define political axis rubric and prompt.
  - [ ] Select pairwise comparisons by information gain.
  - [ ] Compute outlet bias positions (x, y) with minimal comparisons.
  - [ ] Method: LLM comparisons + Bayesian updates or Elo-style ranking.
- [ ] 4) Backend APIs
  - [ ] Define data model (Article, Outlet, Event, BiasScore).
  - [ ] Implement APIs for status, events, and dev panel metrics.
  - [ ] Method: Spring Boot + PostgreSQL; OpenAPI for contracts.
- [ ] 5) Frontend (dev panel first)
  - [ ] Build reusable UI components (cards, tables, charts, filters).
  - [ ] Dev panel: ingestion counts by outlet/day and matching diagnostics.
  - [ ] Method: Angular + charting library (e.g., Chart.js).
- [ ] 6) Frontend (product)
  - [ ] Landing page map with event-focused coloring.
  - [ ] Event detail view with outlets and bias distribution.
  - [ ] Search and filter experience for events and outlets.
  - [ ] Method: Angular + Leaflet/Mapbox for map.
- [ ] 7) Infrastructure and demo
  - [ ] Package services and schedule jobs.
  - [ ] Add monitoring for ingestion freshness.
  - [ ] Prepare a recruiter-ready demo dataset and walkthrough.

## Local Dev
Backend (Spring Boot):
```
cd backend
mvn spring-boot:run
```

Frontend (Angular):
```
cd frontend
npm install
npm run start
```

Then open `http://localhost:4200` and the UI will call `http://localhost:8080/api/status`.
