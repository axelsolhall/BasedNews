# BasedNews

GroundNews-style news intelligence project. Ingest global news, cluster related stories, surface bias and geographic coverage, and present it in an intuitive, navigable interface.

## Goals
- Ingest news from many countries and sources.
- Map articles to shared events/stories.
- Analyze and display bias and geographic coverage.
- Provide a clean UI to explore events, sources, and bias changes.

## MVP Scope (Scandinavia)
- Countries: Norway, Sweden, Denmark.
- Sources: top 5 outlets per country (curated list stored in `data/outlets.json`).
- Language: multilingual ingestion; normalize to a shared representation so events are comparable across languages.

## Stack Options
- Java + Spring Boot for backend, TypeScript + Angular for frontend.
- Python for ML/AI tasks (clustering, bias scoring, NLP pipelines).

## Roadmap
- [ ] 2) Data ingestion pipeline
  - [ ] Inventory sources (RSS, public APIs, scrapers) by country.
  - [ ] Build ingestion service with dedupe and scheduling.
  - [ ] Normalize article metadata (title, author, outlet, publish time, language, region).
  - [ ] Method: Spring Boot ingestion service + PostgreSQL; optional Python ETL scripts.
- [ ] 3) Event clustering / story mapping
  - [ ] Define clustering objective and evaluation metrics.
  - [ ] Build embeddings pipeline (multilingual).
  - [ ] Cluster articles into events; store event graph.
  - [ ] Method: multilingual embeddings (sentence transformers) + FAISS; periodic batch jobs; store results in DB.
- [ ] 4) Bias and coverage analysis
  - [ ] Choose bias signals (outlet labels, sentiment, framing).
  - [ ] Create bias scoring rubric and explainability notes.
  - [ ] Compute geographic coverage gaps per event.
  - [ ] Method: Python NLP pipeline + curated outlet metadata table.
- [ ] 5) Backend APIs
  - [ ] Design data model (Article, Outlet, Event, Region, BiasScore).
  - [ ] Implement REST/GraphQL endpoints for event browse and detail.
  - [ ] Add search and filtering (country, topic, bias, date).
  - [ ] Method: Spring Boot + PostgreSQL; OpenAPI for contracts.
- [ ] 6) Frontend experience
  - [ ] Build event feed with clustering summaries.
  - [ ] Create event detail view (timeline, outlets, map, bias distribution).
  - [ ] Add interactive filters and comparison views.
  - [ ] Method: Angular + D3/Mapbox or Leaflet for maps.
- [ ] 7) Infrastructure and ops
  - [ ] Set up local dev environment and CI.
  - [ ] Containerize services and schedule jobs.
  - [ ] Monitor data freshness and pipeline errors.
  - [ ] Method: Docker + GitHub Actions; cron or workflow scheduler.
- [ ] 8) Demo and polish
  - [ ] Seed dataset for demo.
  - [ ] Write README usage + architecture docs.
  - [ ] Prepare recruiter-friendly demo script.
  - [ ] Method: scripted demo + screenshots/GIFs.

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
