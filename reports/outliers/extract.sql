-- sqlite3 reports/outliers/v.sqlite < reports/outliers/extract.sql   (≈ 1 min; read-only on the history DB)
ATTACH 'file:data/history/history.sqlite?mode=ro' AS h;
CREATE TABLE v AS SELECT c.call_id, substr(c.event_id,1,10) day, c.event_id, c.first_outlet a, c.second_outlet b, c.first_article fa, c.second_article fb, c.direction dir, c.text_kind kind,
  max(CASE WHEN j.axis='econ' THEN j.first_wins END) econ, max(CASE WHEN j.axis='social' THEN j.first_wins END) social,
  max(CASE WHEN j.axis='econ' THEN j.verdict END) ev, max(CASE WHEN j.axis='social' THEN j.verdict END) sv
FROM h.judge_calls c JOIN h.judgments j USING(call_id) WHERE c.purpose='history' GROUP BY c.call_id;
CREATE INDEX va ON v(a); CREATE INDEX vb ON v(b);
