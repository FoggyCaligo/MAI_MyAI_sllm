//! Read-only compatibility adapter for the existing Python MAI memory.sqlite3.
//! This module never migrates, initializes, or changes the user's database.
//! Only the authenticated owner's fact/utterance nodes are observable.
use crate::{Registry, Tool, ToolSchema};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};
use serde_json::{json, Value};
use std::{
    collections::BTreeSet,
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};

#[derive(Clone)]
pub struct NativeMemory {
    file: Arc<PathBuf>,
    owner: Arc<String>,
}
impl NativeMemory {
    pub fn new(file: impl AsRef<Path>, owner: impl Into<String>) -> Result<Self, String> {
        let owner = owner.into();
        if owner.trim().is_empty() {
            return Err("memory owner is required".into());
        }
        let file = std::fs::canonicalize(file).map_err(|e| format!("memory database: {e}"))?;
        if !file.is_file() {
            return Err("memory database is not a file".into());
        }
        let this = Self {
            file: Arc::new(file),
            owner: Arc::new(owner),
        };
        // Refuse to silently treat a nonexistent or non-MAI sqlite DB as empty memory.
        this.open()?;
        Ok(this)
    }
    fn open(&self) -> Result<Connection, String> {
        let connection = Connection::open_with_flags(
            self.file.as_path(),
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|e| format!("read-only memory open: {e}"))?;
        connection
            .busy_timeout(Duration::from_secs(3))
            .map_err(|e| e.to_string())?;
        let known:i64=connection.query_row("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('nodes','edges','user_anchors','memory_concept_exact')",[],|r|r.get(0))
            .map_err(|e|e.to_string())?;
        if known != 4 {
            return Err("unsupported MAI memory schema; refusing to invent results".into());
        }
        Ok(connection)
    }
    pub fn search(&self, input: &Value) -> Result<Value, String> {
        let obj = input
            .as_object()
            .ok_or("memory_search arguments must be an object")?;
        if obj
            .keys()
            .any(|k| !["query", "keyword", "node_id", "limit"].contains(&k.as_str()))
        {
            return Err("unknown memory_search parameter".into());
        }
        let query = string_param(input, "query")?;
        let keyword = string_param(input, "keyword")?;
        if query.is_some() && keyword.is_some() {
            return Err("query and keyword cannot be combined".into());
        }
        let node_id = integer_param(input, "node_id")?;
        let limit = integer_param(input, "limit")?;
        if let Some(value) = limit {
            if !(1..=50).contains(&value) {
                return Err("memory search limit must be 1..50".into());
            }
        }
        if let Some(id) = node_id {
            if id < 1 || query.is_some() || keyword.is_some() || limit.is_some() {
                return Err("node_id cannot combine with other selectors".into());
            }
            return self.expand(id);
        }
        let db = self.open()?;
        if let Some(text) = query.or(keyword) {
            if text.trim().is_empty() {
                return Err("query must not be empty".into());
            }
            let expr = parse_boolean(text)?;
            let matched = evaluate(&db, self.owner.as_str(), &expr)?;
            let max = limit.unwrap_or(50) as usize;
            let mut nodes = Vec::new();
            for id in matched.into_iter().take(max) {
                if let Some(node) = load_owned(&db, self.owner.as_str(), id)? {
                    nodes.push(node);
                }
            }
            return Ok(json!({"nodes":nodes}));
        }
        let limit = limit.ok_or("limit required for recent overview")?;
        let anchor:Option<(i64,String,String)>=db.query_row(
            "SELECT n.id,n.canonical_text,n.payload_json FROM user_anchors a JOIN nodes n ON n.id=a.node_id WHERE a.user_id=?1",
            params![self.owner.as_str()],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?)))
            .optional().map_err(|e|e.to_string())?;
        let Some((anchor_id, anchor_text, payload)) = anchor else {
            return Ok(json!({"user_anchor":null,"memories":[]}));
        };
        let mut stmt=db.prepare(
            "SELECT n.id,n.node_type,n.canonical_text,n.payload_json,n.created_at,n.last_seen_at
             FROM edges e JOIN nodes n ON n.id=e.to_node_id
             WHERE e.from_node_id=?1 AND e.relation IN ('spoke','asserted_fact')
             AND n.node_type IN ('fact','utterance')
             AND json_extract(n.payload_json,'$.user_id')=?2
             ORDER BY n.last_seen_at DESC,n.id DESC LIMIT ?3"
        ).map_err(|e|e.to_string())?;
        let rows = stmt
            .query_map(params![anchor_id, self.owner.as_str(), limit], |r| {
                let payload: String = r.get(3)?;
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                    payload,
                    r.get::<_, String>(4)?,
                    r.get::<_, String>(5)?,
                ))
            })
            .map_err(|e| e.to_string())?;
        let mut memories = Vec::new();
        for row in rows {
            let (id, kind, text, payload, created, seen) = row.map_err(|e| e.to_string())?;
            memories.push(
                json!({"id":id,"type":kind,"text":text,"payload":parse_payload(&payload)?,
                "created_at":created,"last_seen_at":seen}),
            );
        }
        Ok(
            json!({"user_anchor":{"id":anchor_id,"type":"anchor","text":anchor_text,
            "payload":parse_payload(&payload)?},"memories":memories}),
        )
    }
    fn expand(&self, id: i64) -> Result<Value, String> {
        let db = self.open()?;
        let anchor: Option<i64> = db
            .query_row(
                "SELECT node_id FROM user_anchors WHERE user_id=?1",
                params![self.owner.as_str()],
                |r| r.get(0),
            )
            .optional()
            .map_err(|e| e.to_string())?;
        let Some(anchor) = anchor else {
            return Err("user memory anchor does not exist".into());
        };
        if id != anchor && load_owned(&db, self.owner.as_str(), id)?.is_none() {
            return Err("memory node does not belong to the current user".into());
        }
        let mut ids = BTreeSet::new();
        ids.insert(id);
        if id == anchor {
            let mut stmt = db
                .prepare(
                    "SELECT n.id FROM edges e JOIN nodes n ON n.id=e.to_node_id
                 WHERE e.from_node_id=?1 AND e.relation IN ('spoke','asserted_fact')
                 AND n.node_type IN ('fact','utterance')
                 AND json_extract(n.payload_json,'$.user_id')=?2
                 ORDER BY n.last_seen_at DESC,n.id DESC LIMIT 5",
                )
                .map_err(|e| e.to_string())?;
            let rows = stmt
                .query_map(params![id, self.owner.as_str()], |r| r.get::<_, i64>(0))
                .map_err(|e| e.to_string())?;
            for row in rows {
                ids.insert(row.map_err(|e| e.to_string())?);
            }
        } else {
            // Filter every one-hop neighbor by ownership; shared Concept nodes
            // must never disclose another user's connected memories.
            let mut stmt = db
                .prepare(
                    "SELECT DISTINCT n.id FROM edges e JOIN nodes n
                 ON n.id=CASE WHEN e.from_node_id=?1 THEN e.to_node_id ELSE e.from_node_id END
                 WHERE (e.from_node_id=?1 OR e.to_node_id=?1)
                 AND n.node_type IN ('fact','utterance')
                 AND json_extract(n.payload_json,'$.user_id')=?2",
                )
                .map_err(|e| e.to_string())?;
            let rows = stmt
                .query_map(params![id, self.owner.as_str()], |r| r.get::<_, i64>(0))
                .map_err(|e| e.to_string())?;
            for row in rows {
                ids.insert(row.map_err(|e| e.to_string())?);
            }
        }
        let mut nodes = Vec::new();
        for node_id in ids {
            if let Some(node) = load_owned(&db, self.owner.as_str(), node_id)? {
                nodes.push(node);
            }
        }
        Ok(json!({"nodes":nodes}))
    }
}
fn parse_payload(text: &str) -> Result<Value, String> {
    let value: Value =
        serde_json::from_str(text).map_err(|e| format!("invalid saved payload: {e}"))?;
    if !value.is_object() {
        return Err("memory payload must be an object".into());
    }
    Ok(value)
}
fn load_owned(db: &Connection, owner: &str, id: i64) -> Result<Option<Value>, String> {
    let row: Option<(String, String, String, String)> = db
        .query_row(
            "SELECT node_type,canonical_text,payload_json,created_at FROM nodes
         WHERE id=?1 AND node_type IN ('fact','utterance')
         AND json_extract(payload_json,'$.user_id')=?2",
            params![id, owner],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
        )
        .optional()
        .map_err(|e| e.to_string())?;
    let Some((kind, text, payload, created)) = row else {
        return Ok(None);
    };
    let payload = parse_payload(&payload)?;
    let evidence = payload.get("evidence_id").cloned().unwrap_or(Value::Null);
    // Ownership edge provenance must be an actual graph assertion.
    let mut stmt = db
        .prepare(
            "SELECT DISTINCT provenance FROM edges
         WHERE to_node_id=?1 AND relation IN ('spoke','asserted_fact','derived_fact')
         ORDER BY id",
        )
        .map_err(|e| e.to_string())?;
    let sources = stmt
        .query_map(params![id], |r| r.get::<_, String>(0))
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    Ok(Some(json!({"node_id":id,"node_type":kind,"data":text,
        "created_at":created,"provenance":sources,"evidence_id":evidence})))
}
fn string_param<'a>(v: &'a Value, key: &str) -> Result<Option<&'a str>, String> {
    match v.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => Ok(Some(value.as_str())),
        _ => Err(format!("{key} must be a string")),
    }
}
fn integer_param(v: &Value, key: &str) -> Result<Option<i64>, String> {
    match v.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Number(n)) => n
            .as_i64()
            .ok_or_else(|| format!("{key} must be integer"))
            .map(Some),
        _ => Err(format!("{key} must be integer")),
    }
}
#[derive(Debug)]
enum Expr {
    Term(String),
    Not(Box<Expr>),
    And(Box<Expr>, Box<Expr>),
    Or(Box<Expr>, Box<Expr>),
}
#[derive(Clone, Debug, PartialEq)]
enum Tok {
    Term(String),
    Not,
    And,
    Or,
    L,
    R,
}
fn lex(source: &str) -> Result<Vec<Tok>, String> {
    if source.chars().count() > 4096 {
        return Err("memory query exceeds 4096 chars".into());
    }
    let mut iter = source.chars().peekable();
    let mut words = Vec::new();
    while let Some(ch) = iter.next() {
        if ch.is_whitespace() {
            continue;
        }
        match ch {
            '(' => words.push(Tok::L),
            ')' => words.push(Tok::R),
            '&' => words.push(Tok::And),
            '|' => words.push(Tok::Or),
            '!' => words.push(Tok::Not),
            '"' | '\'' => {
                let mut term = String::new();
                let mut closed = false;
                while let Some(c) = iter.next() {
                    if c == ch {
                        closed = true;
                        break;
                    }
                    if c == '\\' {
                        term.push(iter.next().ok_or("unterminated memory escape")?);
                    } else {
                        term.push(c);
                    }
                }
                if !closed || term.trim().is_empty() {
                    return Err("unterminated or empty quoted term".into());
                }
                words.push(Tok::Term(term.trim().into()));
            }
            _ => {
                let mut term = ch.to_string();
                while let Some(c) = iter.peek() {
                    if c.is_whitespace() || "()&|!\"'".contains(*c) {
                        break;
                    }
                    term.push(*c);
                    iter.next();
                }
                words.push(match term.to_ascii_uppercase().as_str() {
                    "AND" => Tok::And,
                    "OR" => Tok::Or,
                    "NOT" => Tok::Not,
                    _ => Tok::Term(term),
                });
            }
        }
        if words.len() > 192 {
            return Err("memory query has too many tokens".into());
        }
    }
    if words.is_empty() {
        return Err("memory query cannot be empty".into());
    }
    Ok(words)
}
struct Parser {
    tokens: Vec<Tok>,
    position: usize,
    terms: usize,
}
impl Parser {
    fn eat(&mut self, t: &Tok) -> bool {
        if self.tokens.get(self.position) == Some(t) {
            self.position += 1;
            true
        } else {
            false
        }
    }
    fn primary(&mut self, depth: usize) -> Result<Expr, String> {
        if depth > 32 {
            return Err("memory query nesting too deep".into());
        }
        if self.eat(&Tok::L) {
            let value = self.union(depth + 1)?;
            if !self.eat(&Tok::R) {
                return Err("missing memory query closing parenthesis".into());
            }
            return Ok(value);
        }
        if let Some(Tok::Term(value)) = self.tokens.get(self.position) {
            self.terms += 1;
            if self.terms > 64 {
                return Err("too many memory query terms".into());
            }
            let value = value.clone();
            self.position += 1;
            return Ok(Expr::Term(value));
        }
        Err("expected memory search term".into())
    }
    fn negate(&mut self, d: usize) -> Result<Expr, String> {
        if d > 32 {
            return Err("memory query nesting too deep".into());
        }
        if self.eat(&Tok::Not) {
            return Ok(Expr::Not(Box::new(self.negate(d + 1)?)));
        }
        self.primary(d)
    }
    fn intersection(&mut self, d: usize) -> Result<Expr, String> {
        let mut a = self.negate(d)?;
        while self.eat(&Tok::And) {
            a = Expr::And(Box::new(a), Box::new(self.negate(d)?));
        }
        Ok(a)
    }
    fn union(&mut self, d: usize) -> Result<Expr, String> {
        let mut a = self.intersection(d)?;
        while self.eat(&Tok::Or) {
            a = Expr::Or(Box::new(a), Box::new(self.intersection(d)?));
        }
        Ok(a)
    }
}
fn parse_boolean(query: &str) -> Result<Expr, String> {
    let tokens = lex(query)?;
    if !tokens
        .iter()
        .any(|t| matches!(t, Tok::And | Tok::Or | Tok::Not | Tok::L | Tok::R))
    {
        if tokens.len() == 1 {
            if let Tok::Term(v) = &tokens[0] {
                return Ok(Expr::Term(v.clone()));
            }
        }
        if query.contains('"') || query.contains('\'') {
            return Err("multiple quoted terms require AND or OR".into());
        }
        return Ok(Expr::Term(query.trim().into()));
    }
    let mut parser = Parser {
        tokens,
        position: 0,
        terms: 0,
    };
    let value = parser.union(0)?;
    if parser.position != parser.tokens.len() {
        return Err("unexpected memory query token".into());
    }
    Ok(value)
}
fn universe(db: &Connection, owner: &str) -> Result<BTreeSet<i64>, String> {
    let mut stmt = db
        .prepare(
            "SELECT id FROM nodes WHERE node_type IN ('fact','utterance')
        AND json_extract(payload_json,'$.user_id')=?1",
        )
        .map_err(|e| e.to_string())?;
    stmt.query_map(params![owner], |r| r.get::<_, i64>(0))
        .map_err(|e| e.to_string())?
        .collect::<Result<BTreeSet<_>, _>>()
        .map_err(|e| e.to_string())
}
fn term_matches(db: &Connection, owner: &str, term: &str) -> Result<BTreeSet<i64>, String> {
    // Existing Python uses exact concept matches and FTS5 phrase fallback.
    // This first slice deliberately uses the exact index only; return an
    // explicit partial-coverage signal via docs, never pretend embedding parity.
    let chunks = term.split_whitespace().collect::<Vec<_>>();
    let mut found = BTreeSet::new();
    for chunk in chunks {
        let mut stmt = db
            .prepare(
                "SELECT DISTINCT n.id
             FROM memory_concept_exact c JOIN edges e ON e.to_node_id=c.node_id
             JOIN nodes n ON n.id=e.from_node_id
             WHERE c.canonical_text=?1 AND e.relation='mentions'
             AND n.node_type IN ('fact','utterance')
             AND json_extract(n.payload_json,'$.user_id')=?2",
            )
            .map_err(|e| e.to_string())?;
        let ids = stmt
            .query_map(params![chunk, owner], |r| r.get::<_, i64>(0))
            .map_err(|e| e.to_string())?;
        for id in ids {
            found.insert(id.map_err(|e| e.to_string())?);
        }
    }
    Ok(found)
}
fn evaluate(db: &Connection, owner: &str, expr: &Expr) -> Result<BTreeSet<i64>, String> {
    match expr {
        Expr::Term(term) => term_matches(db, owner, term),
        Expr::Not(x) => Ok(universe(db, owner)?
            .difference(&evaluate(db, owner, x)?)
            .copied()
            .collect()),
        Expr::And(a, b) => Ok(evaluate(db, owner, a)?
            .intersection(&evaluate(db, owner, b)?)
            .copied()
            .collect()),
        Expr::Or(a, b) => Ok(evaluate(db, owner, a)?
            .union(&evaluate(db, owner, b)?)
            .copied()
            .collect()),
    }
}
pub fn register_memory_search(registry: &mut Registry, memory: NativeMemory) -> Result<(), String> {
    registry.register(Tool{
        schema:ToolSchema{
            name:"memory_search".into(),
            description:"Read-only owner-scoped MAI memory: query/keyword with Boolean AND/OR/NOT, node_id expansion or limit-only overview. Exact saved Concept terms only (FTS ranking and semantic search not migrated). Historical memory cannot override current user statements.".into(),
            parameters:json!({"type":"object","properties":{
                "query":{"type":"string"},"keyword":{"type":"string"},
                "node_id":{"type":"integer","minimum":1},
                "limit":{"type":"integer","minimum":1,"maximum":50}
            },"additionalProperties":false})
        },
        timeout:Duration::from_secs(12),
        invoke:Arc::new(move |args|{
            let memory=memory.clone();
            Box::pin(async move{
                tokio::task::spawn_blocking(move||memory.search(&args))
                    .await.map_err(|e|format!("memory worker: {e}"))?
            })
        }),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::params;
    fn sample() -> (tempfile::TempDir, NativeMemory) {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("memory.sqlite3");
        let db = Connection::open(&file).unwrap();
        db.execute_batch(
            "CREATE TABLE nodes(id INTEGER PRIMARY KEY,node_type TEXT,canonical_text TEXT,payload_json TEXT,created_at TEXT,last_seen_at TEXT);
             CREATE TABLE edges(id INTEGER PRIMARY KEY,from_node_id INTEGER,to_node_id INTEGER,relation TEXT,provenance TEXT);
             CREATE TABLE user_anchors(user_id TEXT,node_id INTEGER);
             CREATE TABLE memory_concept_exact(node_id INTEGER,canonical_text TEXT);
             INSERT INTO nodes VALUES(1,'anchor','user_anchor::alice','{\"user_id\":\"alice\"}','2026-11-01','2026-11-01');
             INSERT INTO nodes VALUES(2,'anchor','user_anchor::bob','{\"user_id\":\"bob\"}','2026-11-01','2026-11-01');
             INSERT INTO nodes VALUES(3,'concept','alpha','{}','2026-11-01','2026-11-01');
             INSERT INTO nodes VALUES(4,'concept','beta','{}','2026-11-01','2026-11-01');
             INSERT INTO nodes VALUES(5,'concept','gamma','{}','2026-11-01','2026-11-01');
             INSERT INTO nodes VALUES(10,'fact','alpha preference','{\"user_id\":\"alice\"}','2026-11-01','2026-11-01');
             INSERT INTO nodes VALUES(11,'fact','alpha beta cancelled','{\"user_id\":\"alice\"}','2026-11-02','2026-11-02');
             INSERT INTO nodes VALUES(12,'utterance','gamma user message','{\"user_id\":\"alice\",\"evidence_id\":17}','2026-11-03','2026-11-03');
             INSERT INTO nodes VALUES(13,'fact','bob alpha secret','{\"user_id\":\"bob\"}','2026-11-03','2026-11-03');
             INSERT INTO user_anchors VALUES('alice',1);
             INSERT INTO user_anchors VALUES('bob',2);
             INSERT INTO memory_concept_exact VALUES(3,'alpha');
             INSERT INTO memory_concept_exact VALUES(4,'beta');
             INSERT INTO memory_concept_exact VALUES(5,'gamma');
             INSERT INTO edges VALUES(1,1,10,'asserted_fact','user_assertion');
             INSERT INTO edges VALUES(2,1,11,'asserted_fact','user_assertion');
             INSERT INTO edges VALUES(3,1,12,'spoke','user_utterance');
             INSERT INTO edges VALUES(4,2,13,'asserted_fact','user_assertion');
             INSERT INTO edges VALUES(5,10,3,'mentions','index');
             INSERT INTO edges VALUES(6,11,3,'mentions','index');
             INSERT INTO edges VALUES(7,11,4,'mentions','index');
             INSERT INTO edges VALUES(8,12,5,'mentions','index');
             INSERT INTO edges VALUES(9,13,3,'mentions','index');").unwrap();
        drop(db);
        let memory = NativeMemory::new(file, "alice").unwrap();
        (dir, memory)
    }
    #[test]
    fn boolean_filters_are_scoped_to_authenticated_owner() {
        let (_dir, mem) = sample();
        let a = mem.search(&json!({"query":"alpha AND NOT beta"})).unwrap();
        let rows = a["nodes"].as_array().unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0]["node_id"], 10);
        let both = mem.search(&json!({"query":"alpha OR gamma"})).unwrap();
        assert_eq!(both["nodes"].as_array().unwrap().len(), 3);
        let minus = mem.search(&json!({"query":"NOT alpha"})).unwrap();
        assert_eq!(minus["nodes"].as_array().unwrap().len(), 1);
        assert!(mem
            .search(&json!({"node_id":13}))
            .unwrap_err()
            .contains("belong"));
        assert!(mem
            .search(&json!({"node_id":3}))
            .unwrap_err()
            .contains("belong"));
    }
    #[test]
    fn overview_and_node_expansion_do_not_leak_bob() {
        let (_dir, mem) = sample();
        let ov = mem.search(&json!({"limit":2})).unwrap();
        assert_eq!(ov["memories"].as_array().unwrap().len(), 2);
        let node = mem.search(&json!({"node_id":10})).unwrap();
        assert_eq!(node["nodes"][0]["node_id"], 10);
        let anchor = mem.search(&json!({"node_id":1})).unwrap();
        assert!(anchor["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .all(|n| n["node_id"] != 13 && n["node_id"] != 3));
        assert_eq!(ov["user_anchor"]["id"], 1);
    }
    #[test]
    fn invalid_selectors_and_query_depth_are_rejected() {
        let (_dir, mem) = sample();
        for input in [
            json!({"query":"alpha","keyword":"beta"}),
            json!({"node_id":10,"limit":1}),
            json!({"limit":51}),
            json!({}),
            json!({"query":"alpha AND"}),
            json!({"query":"(alpha OR beta"}),
            json!({"query":"'alpha' 'beta'"}),
            json!({"query":"alpha","unknown":true}),
        ] {
            assert!(mem.search(&input).is_err(), "{input}");
        }
        let large = format!("{}alpha{}", "!".repeat(40), "");
        assert!(mem.search(&json!({"query":large})).is_err());
    }
    #[test]
    fn memory_open_is_readonly_and_never_creates_missing_database() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("missing.sqlite3");
        assert!(NativeMemory::new(&path, "alice").is_err());
        assert!(!path.exists());
        let (_folder, mem) = sample();
        assert!(mem.file.exists());
        let connection = mem.open().unwrap();
        assert!(connection.execute("DELETE FROM nodes", []).is_err());
        let count: i64 = connection
            .query_row("SELECT count(*) FROM nodes WHERE id=?1", params![13], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(count, 1);
    }
}
