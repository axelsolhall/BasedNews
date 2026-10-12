# ADR-007 — Respect robots.txt and text-and-data-mining opt-outs automatically
**Common practice:** scrape whatever is reachable.
**Decision:** M1 checks robots.txt and W3C TDMRep per outlet; opted-out outlets are processed as headline + summary only.
Article text is never republished. Found: Le Figaro, France 24, Le Parisien, TF1 Info, franceinfo reserve TDM rights.
**Trade-off:** fewer full-text comparisons → handled by the cost/sharpness model (ADR-002, A30).
