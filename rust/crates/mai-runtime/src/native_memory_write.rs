//! Experimental Python-schema-compatible fact writer.
//! Not registered as a model tool. Callers must explicitly opt into a disposable
//! or backed-up database; the production Python DB remains the source of truth.
use rusqlite::{
    params, Connection, OpenFlags, OptionalExtension, Transaction, TransactionBehavior,
};
use serde_json::json;
use std::path::Path;

pub struct FactWriter {
    db: Connection,
    owner: String,
    segments_attached: bool,
}

/// A fact already accepted by the caller's admission policy. Concepts must be
/// supplied by Sentence_Breaker, not inferred by this storage adapter.
pub struct AdmittedFact {
    pub text: String,
    pub concepts: Vec<String>,
}

#[derive(Debug, PartialEq, Eq)]
pub struct SavedTurn {
    pub evidence_id: i64,
    pub fact_ids: Vec<i64>,
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
            OpenFlags::SQLITE_OPEN_READ_WRITE
                | OpenFlags::SQLITE_OPEN_NO_MUTEX
                | OpenFlags::SQLITE_OPEN_URI,
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
        // Check the columns we actually write before accepting the DB. Merely
        // finding tables with familiar names does not establish compatibility.
        for sql in [
            "SELECT id,identity_key,node_type,canonical_text,payload_json,occurrence_count,created_at,last_seen_at FROM nodes LIMIT 0",
            "SELECT from_node_id,to_node_id,relation,provenance,created_at FROM edges LIMIT 0",
            "SELECT user_id,node_id FROM user_anchors LIMIT 0",
            "SELECT id,kind,content,created_at FROM evidence LIMIT 0",
            "SELECT node_id,canonical_text FROM memory_concept_exact LIMIT 0",
            "SELECT rowid,canonical_text FROM memory_concept_fts LIMIT 0",
        ] {
            db.prepare(sql).map_err(|e| format!("unsupported Python memory schema: {e}"))?;
        }
        Ok(Self {
            db,
            owner: owner.to_string(),
            segments_attached: false,
        })
    }

    pub fn with_sentence_breaker(mut self, path: impl AsRef<Path>) -> Result<Self, String> {
        let path = std::fs::canonicalize(path).map_err(|e| e.to_string())?;
        let main: String = self
            .db
            .query_row(
                "SELECT file FROM pragma_database_list WHERE name='main'",
                [],
                |r| r.get(0),
            )
            .map_err(|e| e.to_string())?;
        if path == std::fs::canonicalize(main).map_err(|e| e.to_string())? {
            return Err("memory and Sentence_Breaker databases must be separate files".into());
        }
        crate::sentence_breaker::attach(&self.db, &path)?;
        self.segments_attached = true;
        Ok(self)
    }

    /// Complete the admitted turn using the original Sentence_Breaker algorithm.
    /// Learning, evidence, facts and indexes share one attached-DB transaction.
    /// Ordinary errors/cancellation roll back both DBs. Crash atomicity across
    /// databases in WAL mode is not guaranteed by SQLite.
    pub fn save_extracted_turn(
        &mut self,
        user_text: &str,
        facts: &[String],
        cancel: &tokio_util::sync::CancellationToken,
    ) -> Result<SavedTurn, String> {
        if user_text.trim().is_empty() || facts.len() > 128 {
            return Err("invalid completed memory turn".into());
        }
        if !facts.is_empty() && !self.segments_attached {
            return Err("Sentence_Breaker graph is required for extracted facts".into());
        }
        let mut clean = Vec::new();
        for fact in facts {
            let fact = fact.trim();
            if fact.is_empty() || fact.chars().count() > 8192 {
                return Err("invalid fact length".into());
            }
            if !clean.contains(&fact) {
                clean.push(fact);
            }
        }
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|e| e.to_string())?;
        let anchor = owner_anchor(&tx, &self.owner)?;
        let now = timestamp();
        if cancel.is_cancelled() {
            return Err("memory update cancelled".into());
        }
        tx.execute(
            "INSERT INTO evidence(kind,content,created_at) VALUES ('user_utterance',?1,?2)",
            params![user_text, now],
        )
        .map_err(|e| e.to_string())?;
        let evidence_id = tx.last_insert_rowid();
        let mut fact_ids = Vec::new();
        for fact in clean {
            if cancel.is_cancelled() {
                return Err("memory update cancelled".into());
            }
            let segments = crate::sentence_breaker::segment_and_ingest(&tx, fact)?;
            let segments: Vec<_> = segments
                .into_iter()
                .filter(|s| !s.trim().is_empty())
                .collect();
            let (fact, concepts) = validate_fact(fact, &segments)?;
            fact_ids.push(save_fact(&tx, &self.owner, anchor, fact, &concepts, &now)?);
        }
        if cancel.is_cancelled() {
            return Err("memory update cancelled".into());
        }
        tx.commit().map_err(|e| e.to_string())?;
        Ok(SavedTurn {
            evidence_id,
            fact_ids,
        })
    }

    /// Save an already admitted fact. Concept segments must come from the
    /// original Sentence_Breaker; this adapter does not guess segmentation.
    /// One transaction updates the graph and both Concept indexes atomically.
    pub fn save_fact(&mut self, fact: &str, concepts: &[String]) -> Result<i64, String> {
        let (fact, concepts) = validate_fact(fact, concepts)?;
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|e| e.to_string())?;
        let anchor_id = owner_anchor(&tx, &self.owner)?;
        let id = save_fact(&tx, &self.owner, anchor_id, fact, &concepts, &timestamp())?;
        tx.commit().map_err(|e| e.to_string())?;
        Ok(id)
    }

    /// Persist a completed, admitted turn atomically, including its raw user
    /// evidence. This is deliberately not a model tool or an admission policy.
    /// An empty fact list preserves raw evidence (e.g. extraction failed).
    /// Recall-only turns without new facts must be filtered by the caller.
    /// Like Python finish_turn, this does not invent Utterance graph nodes.
    pub fn save_turn(
        &mut self,
        user_text: &str,
        facts: &[AdmittedFact],
    ) -> Result<SavedTurn, String> {
        if user_text.trim().is_empty() {
            return Err("user evidence must be nonempty".into());
        }
        if facts.len() > 128 {
            return Err("at most 128 facts per turn".into());
        }
        let mut clean = Vec::new();
        for fact in facts {
            let normalized = validate_fact(&fact.text, &fact.concepts)?;
            // Extraction returns distinct facts in the Python turn lifecycle.
            // Reject conflicting segments rather than silently dropping data.
            if let Some((_, segments)) = clean.iter().find(|(text, _)| *text == normalized.0) {
                if *segments != normalized.1 {
                    return Err("duplicate fact has different concept segments".into());
                }
            } else {
                clean.push(normalized);
            }
        }
        let tx = self
            .db
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|e| e.to_string())?;
        let anchor_id = owner_anchor(&tx, &self.owner)?;
        let now = timestamp();
        tx.execute(
            "INSERT INTO evidence(kind,content,created_at) VALUES ('user_utterance',?1,?2)",
            params![user_text, now],
        )
        .map_err(|e| e.to_string())?;
        let evidence_id = tx.last_insert_rowid();
        let mut fact_ids = Vec::new();
        for (text, concepts) in clean {
            fact_ids.push(save_fact(
                &tx,
                &self.owner,
                anchor_id,
                text,
                &concepts,
                &now,
            )?);
        }
        tx.commit().map_err(|e| e.to_string())?;
        Ok(SavedTurn {
            evidence_id,
            fact_ids,
        })
    }
}

fn timestamp() -> String {
    // Match Python datetime.isoformat(): fixed microseconds and UTC +00:00.
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Micros, false)
}

fn validate_fact<'a>(
    fact: &'a str,
    concepts: &'a [String],
) -> Result<(&'a str, Vec<&'a str>), String> {
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
        // Python counts every segment occurrence, including repeated characters
        // before the language graph has learned a longer supported path.
        clean.push(segment);
    }
    Ok((fact, clean))
}

fn owner_anchor(tx: &Transaction<'_>, owner: &str) -> Result<i64, String> {
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
    anchor_id.ok_or_else(|| "owner anchor missing or invalid".into())
}

fn save_fact(
    tx: &Transaction<'_>,
    owner: &str,
    anchor_id: i64,
    fact: &str,
    concepts: &[&str],
    now: &str,
) -> Result<i64, String> {
    let identity = format!("fact:{owner}:{fact}");
    let payload = json!({"user_id":owner}).to_string();
    let changed = tx.execute(
            "INSERT INTO nodes(identity_key,node_type,canonical_text,payload_json,occurrence_count,created_at,last_seen_at)
             VALUES (?1,'fact',?2,?3,1,?4,?4)
             ON CONFLICT(identity_key) DO UPDATE SET
             occurrence_count=occurrence_count+1,last_seen_at=excluded.last_seen_at
             WHERE node_type='fact' AND canonical_text=excluded.canonical_text
             AND json_extract(payload_json,'$.user_id')=json_extract(excluded.payload_json,'$.user_id')",
            params![identity, fact, payload, now],
        )
        .map_err(|e| e.to_string())?;
    if changed != 1 {
        return Err("fact identity conflicts with graph ownership or type".into());
    }
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

    for concept in concepts {
        let concept_key = format!("concept:{concept}");
        let changed = tx.execute(
                "INSERT INTO nodes(identity_key,node_type,canonical_text,payload_json,occurrence_count,created_at,last_seen_at)
                 VALUES (?1,'concept',?2,'{}',1,?3,?3)
                 ON CONFLICT(identity_key) DO UPDATE SET
                 occurrence_count=occurrence_count+1,last_seen_at=excluded.last_seen_at
                 WHERE node_type='concept' AND canonical_text=excluded.canonical_text",
                params![concept_key, concept, now],
            )
            .map_err(|e| e.to_string())?;
        if changed != 1 {
            return Err("concept identity conflicts with graph type or text".into());
        }
        let concept_id: i64 = tx
                .query_row(
                    "SELECT id FROM nodes WHERE identity_key=?1 AND node_type='concept' AND canonical_text=?2",
                    params![concept_key, concept],
                    |r| r.get(0),
                )
                .map_err(|e| e.to_string())?;
        let exact: Option<(i64, String)> = tx
                .query_row(
                    "SELECT node_id,canonical_text FROM memory_concept_exact WHERE node_id=?1 OR canonical_text=?2",
                    params![concept_id, concept],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .optional()
                .map_err(|e| e.to_string())?;
        if exact.is_some_and(|(id, text)| id != concept_id || text != *concept) {
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
            Some(existing) if existing != *concept => {
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
    Ok(fact_id)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::native_memory::NativeMemory;

    pub(super) fn fixture() -> (tempfile::TempDir, FactWriter) {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("memory.sqlite3");
        let db = Connection::open(&path).unwrap();
        // Use the maintained Python schema, including its CHECKs and foreign
        // keys, rather than a permissive handwritten imitation.
        let source = include_str!("../../../../python/mai/memory/graph/schema.py");
        let schema = source
            .split_once("SCHEMA_SQL = \"\"\"")
            .unwrap()
            .1
            .split_once("\"\"\"")
            .unwrap()
            .0;
        db.execute_batch(schema).unwrap();
        db.execute_batch(
            "CREATE TABLE memory_concept_exact(node_id INTEGER PRIMARY KEY,canonical_text TEXT NOT NULL UNIQUE);
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

    fn fact(text: &str, concept: &str) -> AdmittedFact {
        AdmittedFact {
            text: text.into(),
            concepts: vec![concept.into()],
        }
    }

    pub(super) fn snapshot(db: &Connection) -> Vec<Vec<Vec<rusqlite::types::Value>>> {
        [
            "nodes",
            "edges",
            "user_anchors",
            "evidence",
            "memory_concept_exact",
            "memory_concept_fts",
        ]
        .iter()
        .map(|table| {
            let mut stmt = db
                .prepare(&format!("SELECT * FROM {table} ORDER BY rowid"))
                .unwrap();
            let cols = stmt.column_count();
            stmt.query_map([], |row| (0..cols).map(|i| row.get(i)).collect())
                .unwrap()
                .collect::<Result<Vec<_>, _>>()
                .unwrap()
        })
        .collect()
    }

    #[test]
    fn completed_turn_preserves_raw_evidence_and_is_searchable() {
        let (dir, mut writer) = fixture();
        let saved = writer
            .save_turn(
                "  사과와 배를 좋아해.\n",
                &[
                    fact("사과 좋아함", "사과"),
                    fact("배 좋아함", "배"),
                    fact("사과 좋아함", "사과"),
                ],
            )
            .unwrap();
        assert_eq!(saved.fact_ids.len(), 2);
        let evidence: (String, String) = writer
            .db
            .query_row(
                "SELECT kind,content FROM evidence WHERE id=?1",
                [saved.evidence_id],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        assert_eq!(
            evidence,
            ("user_utterance".into(), "  사과와 배를 좋아해.\n".into())
        );
        let count: i64 = writer
            .db
            .query_row(
                "SELECT occurrence_count FROM nodes WHERE id=?1",
                [saved.fact_ids[0]],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(count, 1);
        let alice = NativeMemory::new(dir.path().join("memory.sqlite3"), "alice").unwrap();
        let bob = NativeMemory::new(dir.path().join("memory.sqlite3"), "bob").unwrap();
        assert_eq!(
            alice.search(&json!({"query":"사과 OR 배"})).unwrap()["nodes"]
                .as_array()
                .unwrap()
                .len(),
            2
        );
        assert_eq!(
            bob.search(&json!({"query":"사과 OR 배"})).unwrap()["nodes"],
            json!([])
        );
        let utterances: i64 = writer
            .db
            .query_row(
                "SELECT count(*) FROM nodes WHERE node_type='utterance'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(utterances, 0);
    }

    #[test]
    fn empty_facts_preserve_evidence_without_inventing_graph_nodes() {
        let (_dir, mut writer) = fixture();
        let before = snapshot(&writer.db);
        let saved = writer.save_turn("추출하지 못한 원문", &[]).unwrap();
        assert!(saved.fact_ids.is_empty());
        let after = snapshot(&writer.db);
        for table in [0, 1, 2, 4, 5] {
            assert_eq!(before[table], after[table]);
        }
        assert_eq!(after[3].len(), 1);
    }

    #[test]
    fn later_fact_failure_rolls_back_evidence_graph_indexes_and_existing_counts() {
        let (_dir, mut writer) = fixture();
        writer.save_fact("사과 좋아함", &["사과".into()]).unwrap();
        writer
            .db
            .execute("INSERT INTO memory_concept_exact VALUES (999,'배')", [])
            .unwrap();
        let before = snapshot(&writer.db);
        assert!(writer
            .save_turn(
                "사과와 배",
                &[
                    fact("사과 좋아함", "사과"),
                    fact("새 사실", "신규"),
                    fact("배 좋아함", "배"),
                ]
            )
            .is_err());
        assert_eq!(snapshot(&writer.db), before);
    }

    #[test]
    fn two_owners_share_concepts_but_keep_facts_separate() {
        let (dir, mut alice) = fixture();
        let mut bob = FactWriter::open(dir.path().join("memory.sqlite3"), "bob").unwrap();
        let a = alice
            .save_turn("사과", &[fact("사과 좋아함", "사과")])
            .unwrap();
        let b = bob
            .save_turn("사과", &[fact("사과 좋아함", "사과")])
            .unwrap();
        assert_ne!(a.fact_ids, b.fact_ids);
        let concepts: i64 = alice
            .db
            .query_row(
                "SELECT count(*) FROM nodes WHERE node_type='concept'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(concepts, 1);
        for (owner, own, other) in [
            ("alice", a.fact_ids[0], b.fact_ids[0]),
            ("bob", b.fact_ids[0], a.fact_ids[0]),
        ] {
            let reader = NativeMemory::new(dir.path().join("memory.sqlite3"), owner).unwrap();
            assert_eq!(
                reader.search(&json!({"query":"사과"})).unwrap()["nodes"][0]["node_id"],
                own
            );
            assert!(reader.search(&json!({"node_id":other})).is_err());
        }
    }

    #[test]
    fn existing_python_unicode_payload_is_updated_without_rewriting_metadata() {
        let (dir, _) = fixture();
        let path = dir.path().join("memory.sqlite3");
        let db = Connection::open(&path).unwrap();
        // json.dumps separators and Unicode escaping are not graph identity.
        db.execute("INSERT INTO nodes(identity_key,node_type,canonical_text,payload_json,created_at,last_seen_at)
            VALUES ('anchor:user:사용자','anchor','user_anchor::사용자',?1,'t','t')", [r#"{"user_id": "\uc0ac\uc6a9\uc790"}"#]).unwrap();
        let anchor = db.last_insert_rowid();
        db.execute("INSERT INTO user_anchors VALUES ('사용자',?1)", [anchor])
            .unwrap();
        db.execute("INSERT INTO nodes(identity_key,node_type,canonical_text,payload_json,created_at,last_seen_at)
            VALUES ('fact:사용자:사과','fact','사과',?1,'original','original')", [r#"{"user_id": "\uc0ac\uc6a9\uc790"}"#]).unwrap();
        let id = db.last_insert_rowid();
        let mut writer = FactWriter::open(path, "사용자").unwrap();
        assert_eq!(writer.save_fact("사과", &["사과".into()]).unwrap(), id);
        let (count, created, payload): (i64, String, String) = db
            .query_row(
                "SELECT occurrence_count,created_at,payload_json FROM nodes WHERE id=?1",
                [id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .unwrap();
        assert_eq!(count, 2);
        assert_eq!(created, "original");
        assert_eq!(payload, r#"{"user_id": "\uc0ac\uc6a9\uc790"}"#);
    }

    #[test]
    fn rejects_reverse_exact_index_conflict_and_rolls_back() {
        let (_dir, mut writer) = fixture();
        writer.save_fact("사과", &["사과".into()]).unwrap();
        writer
            .db
            .execute(
                "UPDATE memory_concept_exact SET canonical_text='잘못된값'",
                [],
            )
            .unwrap();
        let before = snapshot(&writer.db);
        assert!(writer.save_turn("사과", &[fact("사과", "사과")]).is_err());
        assert_eq!(snapshot(&writer.db), before);
    }

    #[test]
    fn fts_conflict_rolls_back_turn_and_existing_counts() {
        let (_dir, mut writer) = fixture();
        writer.save_fact("사과", &["사과".into()]).unwrap();
        writer
            .db
            .execute("UPDATE memory_concept_fts SET canonical_text='손상'", [])
            .unwrap();
        let before = snapshot(&writer.db);
        assert!(writer
            .save_turn("원문", &[fact("새 사실", "신규"), fact("사과", "사과")])
            .is_err());
        assert_eq!(snapshot(&writer.db), before);
    }

    #[test]
    fn open_never_creates_db_and_rejects_incompatible_evidence_schema() {
        let (dir, writer) = fixture();
        let missing = dir.path().join("missing.sqlite3");
        assert!(FactWriter::open(&missing, "alice").is_err());
        assert!(!missing.exists());
        writer
            .db
            .execute_batch("DROP TABLE evidence; CREATE TABLE evidence(id INTEGER PRIMARY KEY);")
            .unwrap();
        let before = snapshot(&writer.db);
        assert!(FactWriter::open(dir.path().join("memory.sqlite3"), "alice").is_err());
        assert_eq!(snapshot(&writer.db), before);
    }

    #[test]
    fn rejects_conflicting_owner_and_invalid_batch_without_changes() {
        let (_dir, mut writer) = fixture();
        writer.save_fact("사과", &["사과".into()]).unwrap();
        writer
            .db
            .execute(
                "UPDATE nodes SET payload_json='{\"user_id\":\"bob\"}' WHERE node_type='fact'",
                [],
            )
            .unwrap();
        let before = snapshot(&writer.db);
        assert!(writer.save_turn("사과", &[fact("사과", "사과")]).is_err());
        assert!(writer
            .save_turn("사과", &[fact("배", "배"), fact("", "사과")])
            .is_err());
        assert!(writer
            .save_turn("사과", &[fact("배", "배"), fact("배", "다른 분절")])
            .is_err());
        assert_eq!(snapshot(&writer.db), before);
        writer
            .db
            .execute(
                "UPDATE nodes SET payload_json='{\"user_id\":\"bob\"}' WHERE id=1",
                [],
            )
            .unwrap();
        let before = snapshot(&writer.db);
        assert!(writer.save_turn("원문만", &[]).is_err());
        assert_eq!(snapshot(&writer.db), before);
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

#[cfg(test)]
mod segmented_tests {
    use super::tests::fixture;
    use super::*;
    use tokio_util::sync::CancellationToken;

    fn graph(dir: &Path) -> std::path::PathBuf {
        let path = dir.join("segments.sqlite3");
        Connection::open(&path)
            .unwrap()
            .execute_batch(include_str!(
                "../tests/fixtures/sentence-breaker-schema.sql"
            ))
            .unwrap();
        path
    }
    #[test]
    fn segmentation_learns_only_after_successful_commit() {
        let (dir, writer) = fixture();
        let path = graph(dir.path());
        let mut writer = writer.with_sentence_breaker(&path).unwrap();
        let cancel = CancellationToken::new();
        let first = writer
            .save_extracted_turn("엄마", &["엄마".into()], &cancel)
            .unwrap();
        let second = writer
            .save_extracted_turn("엄마", &["엄마".into()], &cancel)
            .unwrap();
        assert_eq!(first.fact_ids, second.fact_ids);
        let concepts: Vec<String> = writer
            .db
            .prepare("SELECT canonical_text FROM nodes WHERE node_type='concept' ORDER BY id")
            .unwrap()
            .query_map([], |r| r.get(0))
            .unwrap()
            .collect::<Result<_, _>>()
            .unwrap();
        assert_eq!(concepts, vec!["엄", "마", "엄마"]);
        let layers: i64 = Connection::open(path)
            .unwrap()
            .query_row("SELECT count(*) FROM sentence_layers", [], |r| r.get(0))
            .unwrap();
        assert_eq!(layers, 2);
    }
    #[test]
    fn memory_index_failure_rolls_back_sentence_learning_too() {
        let (dir, writer) = fixture();
        let path = graph(dir.path());
        let mut writer = writer.with_sentence_breaker(&path).unwrap();
        writer
            .db
            .execute("INSERT INTO memory_concept_exact VALUES (999,'마')", [])
            .unwrap();
        let before = super::tests::snapshot(&writer.db);
        assert!(writer
            .save_extracted_turn("엄마", &["엄마".into()], &CancellationToken::new())
            .is_err());
        assert_eq!(super::tests::snapshot(&writer.db), before);
        let db = Connection::open(path).unwrap();
        for table in ["alph_nodes", "sentence_layers", "alph_links"] {
            let count: i64 = db
                .query_row(&format!("SELECT count(*) FROM {table}"), [], |r| r.get(0))
                .unwrap();
            assert_eq!(count, 0);
        }
    }
    #[test]
    fn cancelled_turn_does_not_write_or_learn() {
        let (dir, writer) = fixture();
        let path = graph(dir.path());
        let mut writer = writer.with_sentence_breaker(path).unwrap();
        let before = super::tests::snapshot(&writer.db);
        let cancel = CancellationToken::new();
        cancel.cancel();
        assert!(writer
            .save_extracted_turn("엄마", &["엄마".into()], &cancel)
            .is_err());
        assert_eq!(super::tests::snapshot(&writer.db), before);
    }

    #[test]
    fn repeated_segments_keep_python_occurrence_counts() {
        let (dir, writer) = fixture();
        let mut writer = writer.with_sentence_breaker(graph(dir.path())).unwrap();
        writer
            .save_extracted_turn("aaaa", &["aaaa".into()], &CancellationToken::new())
            .unwrap();
        let count: i64 = writer
            .db
            .query_row(
                "SELECT occurrence_count FROM nodes WHERE identity_key='concept:a'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(count, 4);
    }
}
