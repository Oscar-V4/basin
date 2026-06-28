-- Basin derived index (read-model). Rebuildable from .basin/*.jsonl via `basin reindex`.
-- NOT the source of truth. Never written except by reindex.
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE branch (
  branch_id TEXT PRIMARY KEY, name TEXT, intent TEXT, status TEXT,
  base_checkpoint_id TEXT, merge_policy TEXT, head_checkpoint_id TEXT, created_at TEXT
);

CREATE TABLE raw_event (
  id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT, branch_id TEXT,
  event_seq INTEGER, event_type TEXT, occurred_at TEXT, content_text TEXT,
  token_estimate INTEGER, raw_hash TEXT
);
CREATE INDEX ix_raw_event_session ON raw_event(session_id, event_seq);

CREATE TABLE checkpoint (
  id TEXT PRIMARY KEY, project_id TEXT, branch_id TEXT, kind TEXT,
  parent_checkpoint_id TEXT, raw_event_start_seq INTEGER, raw_event_end_seq INTEGER,
  title TEXT, summary_text TEXT, created_by TEXT, created_at TEXT
);
CREATE INDEX ix_checkpoint_branch ON checkpoint(branch_id);

CREATE TABLE atom_revision (
  id TEXT PRIMARY KEY, atom_id TEXT, project_id TEXT, branch_id TEXT, checkpoint_id TEXT,
  semantic_entity_pk TEXT, revision_no INTEGER, visibility TEXT, lifecycle_status TEXT,
  atom_type TEXT, statement TEXT, subject_key TEXT, confidence_score REAL, authority_tier TEXT,
  source_raw_event_id TEXT, source_quote TEXT, provenance TEXT, change_kind TEXT,
  structural_fp TEXT, semantic_fp TEXT,
  cosmetic_fp TEXT, revision_hash TEXT, supersedes_revision_id TEXT, created_by TEXT, created_at TEXT
);
CREATE INDEX ix_rev_atom ON atom_revision(atom_id);

CREATE TABLE atom_ref (
  branch_id TEXT, atom_id TEXT, current_revision_id TEXT,
  visibility TEXT, lifecycle_status TEXT, updated_at TEXT,
  PRIMARY KEY (branch_id, atom_id)
);

CREATE TABLE edge (
  id TEXT PRIMARY KEY, project_id TEXT, branch_id TEXT, src TEXT, dst TEXT,
  relation TEXT, confidence TEXT, source_raw_event_id TEXT, created_at TEXT
);

CREATE TABLE session_link (
  id TEXT PRIMARY KEY, project_id TEXT, provider TEXT, external_session_id TEXT,
  branch_id TEXT, base_checkpoint_id TEXT, base_context_pack_id TEXT,
  parent_session_link_id TEXT, transcript_path TEXT, cwd TEXT, status TEXT,
  started_at TEXT, last_seen_at TEXT
);

CREATE TABLE do_not_load (
  id TEXT PRIMARY KEY, project_id TEXT, branch_id TEXT, atom_id TEXT,
  action TEXT, policy TEXT, reason TEXT, created_by TEXT, created_at TEXT
);

CREATE VIEW v_current_atoms AS
  SELECT r.branch_id, r.atom_id, rev.atom_type, rev.statement, rev.subject_key,
         rev.authority_tier, rev.confidence_score, r.visibility, r.lifecycle_status
  FROM atom_ref r JOIN atom_revision rev ON rev.id = r.current_revision_id
  WHERE r.lifecycle_status IN ('active','released');

CREATE VIEW v_staged_candidates AS
  SELECT r.branch_id, r.atom_id, rev.atom_type, rev.statement, rev.change_kind,
         rev.authority_tier, rev.confidence_score
  FROM atom_ref r JOIN atom_revision rev ON rev.id = r.current_revision_id
  WHERE r.lifecycle_status IN ('candidate','staged');

CREATE VIEW v_effective_do_not_load AS
  SELECT branch_id, atom_id, action, reason FROM do_not_load WHERE action = 'exclude';
