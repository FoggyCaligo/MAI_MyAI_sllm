//! Experimental Python-schema-compatible fact writer.
//! Not registered as a model tool. Callers must explicitly opt into a disposable
//! or backed-up database; the production Python DB remains the source of truth.
use rusqlite::{params, Connection, OpenFlags, OptionalExtension, TransactionBehavior};
use serde_json::json;
use std::path::Path;

pub struct FactWriter {
    db: Connection,
    owner: String,
}

impl FactWriter {
    pub fn open(path: impl AsRef<Path>, owner: &str) -> Result<Self, String> {
        if owner.trim().is_empty() || owner != owner.trim() {
            return Err("nonempty canonical memory owner required".into());
        }
        let path = std::fs::canonicalize(path).map_err(|e| e.to_string())?;
        if !path.is_file() {
            return Err("memory database is not a file".into());
        }
        let db = Connection::open_with_flags(
            path,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|e| e.to_string())?;
        db.busy_timeout(std::time::Duration::from_secs(3))
            .map_err(|e| e.to_string())?;
        db.pragma_update(None, "foreign_keys", "ON")
            .map_err(|e| e.to_string())?;
        for name in [
            "nodes",
            "edges",
            "user_anchors",
            "evidence",
            "memory_concept_exact",
            "memory_concept_fts",
        ] {
            let found: i64 = db
                .query_row(
                    "SELECT count(*) FROM sqlite_master WHERE name=?1 AND type IN ('table','view')",
                    [name],
                    |r| r.get(0),
                )
                .map_err(|e| e.to_string())?;
            if found != 1 {
                return Err(format!("missing Python memory schema object: {name}"));
            }
        }
        Ok(Self {
            db,
            owner: owner.to_string(),
        })
    }

    /// Save an already admitted fact. Concept segments must come from the
    /// original Sentence_Breaker; this adapter does not guess segmentation.
    /// One transaction updates the graph and both Concept indexes atomically.
    pub fn save_fact(&mut self, fact: &str, concepts: &[String]) -> Result<i64, String> {
        let fact = fact.trim();
        if fact.is_empty() || fact.chars().count() > 8192 {
            return Err("invalid fact length".into());
        }
        if concepts.is_empty() || concepts.len() > 128 {
            return Err("Sentence_Breaker concept segments required (1..128)".into());
        }
        let mut clean = Vec::new();
        for segment in concepts {
            let segment = segment.trim();
            if segment.is_empty() || segment.chars().count() > 512 {
                return Err("invalid concept segment".into());
            }
            if !clean.contains(&segment) {
                clean.push(segment);
            }
        }
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|e| e.to_string())?;
        let now = chrono::Utc::now().to_rfc3339();
        let owner = self.owner.as_str();
        let anchor_id: Option<i64> = tx
            .query_row(
                "SELECT n.id FROM user_anchors a JOIN nodes n ON n.id=a.node_id
                 WHERE a.user_id=?1 AND n.node_type='anchor'
                 AND json_extract(n.payload_json,'$.user_id')=?1",
                [owner],
                |r| r.get(0),
            )
            .optional()
            .map_err(|e| e.to_string())?;
        // Never auto-provision ownership in this preview writer.
        let anchor_id = anchor_id.ok_or("owner anchor missing or invalid")?;
        let identity = format!("fact:{owner}:{fact}");
        let payload = json!({"user_id":owner}).to_string();
        tx.execute(
            "INSERT INTO nodes(identity_key,node_type,canonical_text,payload_json,occurrence_count,created_at,last_seen_at)
             VALUES (?1,'fact',?2,?3,1,?4,?4)
             ON CONFLICT(identity_key) DO UPDATE SET
             occurrence_count=occurrence_count+1,last_seen_at=excluded.last_seen_at
             WHERE node_type='fact' AND canonical_text=excluded.canonical_text AND payload_json=excluded.payload_json",
            params![identity, fact, payload, now],
        )
        .map_err(|e| e.to_string())?;
        let fact_id: i64 = tx
            .query_row(
                "SELECT id FROM nodes WHERE identity_key=?1 AND node_type='fact'
                 AND canonical_text=?2 AND json_extract(payload_json,'$.user_id')=?3",
                params![identity, fact, owner],
                |r| r.get(0),
            )
            .map_err(|e| e.to_string())?;
        tx.execute(
            "INSERT OR IGNORE INTO edges(from_node_id,to_node_id,relation,provenance,created_at)
             VALUES (?1,?2,'asserted_fact','user_assertion',?3)",
            params![anchor_id, fact_id, now],
        )
        .map_err(|e| e.to_string())?;

        for concept in clean {
            let concept_key = format!("concept:{concept}");
            tx.execute(
                "INSERT INTO nodes(identity_key,node_type,canonical_text,payload_json,occurrence_count,created_at,last_seen_at)
                 VALUES (?1,'concept',?2,'{}',1,?3,?3)
                 ON CONFLICT(identity_key) DO UPDATE SET
                 occurrence_count=occurrence_count+1,last_seen_at=excluded.last_seen_at
                 WHERE node_type='concept' AND canonical_text=excluded.canonical_text",
                params![concept_key, concept, now],
            )
            .map_err(|e| e.to_string())?;
            let concept_id: i64 = tx
                .query_row(
                    "SELECT id FROM nodes WHERE identity_key=?1 AND node_type='concept' AND canonical_text=?2",
                    params![concept_key, concept],
                    |r| r.get(0),
                )
                .map_err(|e| e.to_string())?;
            let exact: Option<i64> = tx
                .query_row(
                    "SELECT node_id FROM memory_concept_exact WHERE canonical_text=?1",
                    [concept],
                    |r| r.get(0),
                )
                .optional()
                .map_err(|e| e.to_string())?;
            if exact.is_some_and(|id| id != concept_id) {
                return Err("concept exact index conflicts with graph identity".into());
            }
            tx.execute(
                "INSERT OR IGNORE INTO memory_concept_exact(node_id,canonical_text) VALUES (?1,?2)",
                params![concept_id, concept],
            )
            .map_err(|e| e.to_string())?;
            let indexed: Option<String> = tx
                .query_row(
                    "SELECT canonical_text FROM memory_concept_fts WHERE rowid=?1",
                    [concept_id],
                    |r| r.get(0),
                )
                .optional()
                .map_err(|e| e.to_string())?;
            match indexed {
                None => {
                    tx.execute(
                        "INSERT INTO memory_concept_fts(rowid,canonical_text) VALUES (?1,?2)",
                        params![concept_id, concept],
                    )
                    .map_err(|e| e.to_string())?;
                }
                Some(existing) if existing != concept => {
                    return Err("FTS index conflicts with graph identity".into());
                }
                _ => {}
            }
            tx.execute(
                "INSERT OR IGNORE INTO edges(from_node_id,to_node_id,relation,provenance,created_at)
                 VALUES (?1,?2,'mentions','fact_index',?3)",
                params![fact_id, concept_id, now],
            )
            .map_err(|e| e.to_string())?;
        }
        tx.commit().map_err(|e| e.to_string())?;
        Ok(fact_id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> (tempfile::TempDir, FactWriter) {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("memory.sqlite3");
        let db = Connection::open(&path).unwrap();
        db.execute_batch(
            "PRAGMA foreign_keys=ON;
             CREATE TABLE nodes(id INTEGER PRIMARY KEY AUTOINCREMENT, identity_key TEXT NOT NULL UNIQUE,
               node_type TEXT NOT NULL, canonical_text TEXT NOT NULL,payload_json TEXT NOT NULL,
               occurrence_count INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,last_seen_at TEXT NOT NULL);
             CREATE TABLE edges(id INTEGER PRIMARY KEY AUTOINCREMENT,from_node_id INTEGER NOT NULL,
               to_node_id INTEGER NOT NULL,relation TEXT NOT NULL,provenance TEXT NOT NULL,
               created_at TEXT NOT NULL,UNIQUE(from_node_id,to_node_id,relation));
             CREATE TABLE user_anchors(user_id TEXT PRIMARY KEY,node_id INTEGER NOT NULL UNIQUE);
             CREATE TABLE evidence(id INTEGER PRIMARY KEY,kind TEXT,content TEXT,created_at TEXT);
             CREATE TABLE memory_concept_exact(node_id INTEGER PRIMARY KEY,canonical_text TEXT UNIQUE);
             CREATE VIRTUAL TABLE memory_concept_fts USING fts5(canonical_text,tokenize='unicode61');
             INSERT INTO nodes(id,identity_key,node_type,canonical_text,payload_json,created_at,last_seen_at)
             VALUES (1,'anchor:user:alice','anchor','user_anchor::alice','{\"user_id\":\"alice\"}','t','t'),
                    (2,'anchor:user:bob','anchor','user_anchor::bob','{\"user_id\":\"bob\"}','t','t');
             INSERT INTO user_anchors VALUES ('alice',1),('bob',2);",
        )
        .unwrap();
        drop(db);
        let writer = FactWriter::open(&path, "alice").unwrap();
        (dir, writer)
    }

    #[test]
    fn save_is_indexed_deduplicated_and_owner_bound() {
        let (_dir, mut writer) = fixture();
        let concept = vec!["사과".to_string()];
        let id = writer.save_fact("사과 좋아함", &concept).unwrap();
        assert_eq!(id, writer.save_fact("사과 좋아함", &concept).unwrap());
        let db = &writer.db;
        let count: i64 = db
            .query_row(
                "SELECT occurrence_count FROM nodes WHERE id=?1",
                [id],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(count, 2);
        let links: i64 = db.query_row(
            "SELECT count(*) FROM edges WHERE to_node_id=?1 AND from_node_id=1 AND relation='asserted_fact'",
            [id], |r| r.get(0),
        ).unwrap();
        assert_eq!(links, 1);
        let found: i64 = db
            .query_row(
                "SELECT count(*) FROM memory_concept_fts WHERE memory_concept_fts MATCH '\"사과\"'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(found, 1);
        let bob: i64 = db
            .query_row("SELECT count(*) FROM edges WHERE from_node_id=2", [], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(bob, 0);
    }

    #[test]
    fn failed_index_update_rolls_back_all_changes() {
        let (_dir, mut writer) = fixture();
        writer
            .db
            .execute(
                "INSERT INTO memory_concept_exact(node_id,canonical_text) VALUES (999,'사과')",
                [],
            )
            .unwrap();
        assert!(writer.save_fact("사과 좋아함", &["사과".into()]).is_err());
        let count: i64 = writer
            .db
            .query_row(
                "SELECT count(*) FROM nodes WHERE node_type='fact'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(count, 0);
    }

    #[test]
    fn missing_owner_cannot_write() {
        let (_dir, mut writer) = fixture();
        writer.owner = "charlie".into();
        assert!(writer.save_fact("fact", &["concept".into()]).is_err());
    }
}
