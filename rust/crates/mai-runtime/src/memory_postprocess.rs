//! Post-answer fact extraction, admission and transactional persistence.
//! Memory reads are excluded from new extraction evidence, as in Python MAI.
use crate::{
    llamacpp::{LlamaConfig, LlamaCpp},
    native_memory_write::FactWriter,
    BoxFuture, Message, Outcome, Role,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{path::PathBuf, time::Duration};
use tokio_util::sync::CancellationToken;

const EXTRACTION_SYSTEM: &str = include_str!("../prompts/fact_extraction.txt");

#[derive(Clone, Debug)]
pub struct CompletedTurn {
    pub user_text: String,
    pub previous_assistant_message: Option<String>,
    pub final_answer: String,
    tools: Vec<ToolResult>,
}
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ToolResult {
    name: String,
    ok: bool,
    result: Value,
}

impl CompletedTurn {
    /// Read actual model-visible tool envelopes, not the UI's best-effort event
    /// drain (which can lag the final event). Prior chat cannot contain tools.
    pub fn from_outcome(
        user_text: &str,
        previous: &[Message],
        outcome: &Outcome,
    ) -> Result<Self, String> {
        if user_text.trim().is_empty() || outcome.answer.trim().is_empty() {
            return Err("completed memory turn requires user text and final answer".into());
        }
        let tools = outcome
            .messages
            .iter()
            .filter(|m| m.role == Role::Tool)
            .map(|m| {
                serde_json::from_str::<ToolResult>(&m.content)
                    .map_err(|e| format!("invalid completed tool envelope: {e}"))
            })
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Self {
            user_text: user_text.into(),
            previous_assistant_message: previous
                .iter()
                .rev()
                .find(|m| m.role == Role::Assistant)
                .map(|m| m.content.clone()),
            final_answer: outcome.answer.clone(),
            tools,
        })
    }
    fn extraction_payload(&self) -> Value {
        json!({"latest_user_message":self.user_text,"previous_assistant_message":self.previous_assistant_message,
            "assistant_final_answer":self.final_answer,"successful_tool_results":self.tools.iter()
            .filter(|t|t.ok && t.name!="memory_search").map(|t|t.result.to_string()).collect::<Vec<_>>()})
    }
    fn used_recall(&self) -> bool {
        self.tools.iter().any(|t| t.ok && t.name == "memory_search")
    }
}

pub trait FactExtractor: Send {
    fn extract<'a>(
        &'a mut self,
        turn: &'a CompletedTurn,
    ) -> BoxFuture<'a, Result<Vec<String>, String>>;
}
pub struct LlamaFactExtractor {
    model: LlamaCpp,
}
impl LlamaFactExtractor {
    pub fn new(config: LlamaConfig) -> Result<Self, String> {
        Ok(Self {
            model: LlamaCpp::new(config)?,
        })
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FactPayload {
    facts: Vec<String>,
}
fn normalize_facts(facts: Vec<String>) -> Result<Vec<String>, String> {
    if facts.len() > 128 {
        return Err("too many extracted facts".into());
    }
    let mut clean = Vec::new();
    for fact in facts {
        let fact = fact.trim();
        if fact.is_empty() || fact.chars().count() > 8192 {
            return Err("invalid extracted fact length".into());
        }
        if !clean.iter().any(|s| s == fact) {
            clean.push(fact.to_string());
        }
    }
    Ok(clean)
}
impl FactExtractor for LlamaFactExtractor {
    fn extract<'a>(
        &'a mut self,
        turn: &'a CompletedTurn,
    ) -> BoxFuture<'a, Result<Vec<String>, String>> {
        Box::pin(async move {
            let text=self.model.structured(EXTRACTION_SYSTEM.trim(),turn.extraction_payload(),json!({
                "type":"object","properties":{"facts":{"type":"array","items":{"type":"string"}}},
                "required":["facts"],"additionalProperties":false
            })).await?;
            let payload: FactPayload = serde_json::from_str(&text)
                .map_err(|e| format!("invalid fact extraction schema: {e}"))?;
            normalize_facts(payload.facts)
        })
    }
}

#[derive(Clone, Debug)]
pub struct MemoryPipeline {
    pub memory_db: PathBuf,
    pub segment_db: PathBuf,
    pub owner: String,
    pub extraction_timeout: Duration,
}
#[derive(Debug, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum MemoryUpdate {
    SkippedRecall,
    Stored {
        evidence_id: i64,
        fact_ids: Vec<i64>,
        extraction_error: Option<String>,
    },
}
impl MemoryPipeline {
    pub async fn process(
        &self,
        turn: CompletedTurn,
        extractor: &mut impl FactExtractor,
        cancel: &CancellationToken,
    ) -> Result<MemoryUpdate, String> {
        if self.extraction_timeout.is_zero() {
            return Err("extraction timeout must be positive".into());
        }
        let extraction = tokio::select! {
            biased;
            _=cancel.cancelled()=>return Err("memory update cancelled".into()),
            result=tokio::time::timeout(self.extraction_timeout,extractor.extract(&turn))=>match result {
                Ok(result)=>result.and_then(normalize_facts),
                Err(_)=>Err("fact extraction timed out".into()),
            }
        };
        if cancel.is_cancelled() {
            return Err("memory update cancelled".into());
        }
        let (facts, extraction_error) = match extraction {
            Ok(facts) if facts.is_empty() && turn.used_recall() => {
                return Ok(MemoryUpdate::SkippedRecall)
            }
            Ok(facts) => (facts, None),
            Err(error) => (vec![], Some(error)), // Preserve raw evidence, expose failed extraction.
        };
        let config = self.clone();
        let cancel = cancel.clone();
        // Do not detach blocking SQLite writes on cancellation. The writer
        // observes the token and completes commit/rollback before we return.
        tokio::task::spawn_blocking(move || {
            let mut writer = FactWriter::open(&config.memory_db, &config.owner)?;
            if !facts.is_empty() {
                writer = writer.with_sentence_breaker(&config.segment_db)?;
            }
            let saved = writer.save_extracted_turn(&turn.user_text, &facts, &cancel)?;
            Ok(MemoryUpdate::Stored {
                evidence_id: saved.evidence_id,
                fact_ids: saved.fact_ids,
                extraction_error,
            })
        })
        .await
        .map_err(|e| format!("memory persistence worker: {e}"))?
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;
    fn fixture() -> (tempfile::TempDir, MemoryPipeline) {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("memory.sqlite3");
        let db = Connection::open(&path).unwrap();
        let source = include_str!("../../../../python/mai/memory/graph/schema.py");
        db.execute_batch(
            source
                .split_once("SCHEMA_SQL = \"\"\"")
                .unwrap()
                .1
                .split_once("\"\"\"")
                .unwrap()
                .0,
        )
        .unwrap();
        db.execute_batch("CREATE TABLE memory_concept_exact(node_id INTEGER PRIMARY KEY,canonical_text TEXT NOT NULL UNIQUE);
            CREATE VIRTUAL TABLE memory_concept_fts USING fts5(canonical_text,tokenize='unicode61');
            INSERT INTO nodes VALUES(1,'anchor:user:db-alice','anchor','user_anchor::db-alice','{\"user_id\":\"db-alice\"}',1,'t','t');
            INSERT INTO user_anchors VALUES ('db-alice',1);").unwrap();
        let segment_db = dir.path().join("segment.sqlite3");
        Connection::open(&segment_db)
            .unwrap()
            .execute_batch(include_str!(
                "../tests/fixtures/sentence-breaker-schema.sql"
            ))
            .unwrap();
        let pipeline = MemoryPipeline {
            memory_db: path,
            segment_db,
            owner: "db-alice".into(),
            extraction_timeout: Duration::from_secs(90),
        };
        (dir, pipeline)
    }
    fn turn(tools: Vec<(&str, bool, Value)>) -> CompletedTurn {
        let previous = vec![Message {
            role: Role::Assistant,
            content: "previous approved topic".into(),
        }];
        let mut messages = previous.clone();
        messages.push(Message {
            role: Role::User,
            content: "current correction".into(),
        });
        messages.extend(tools.into_iter().map(|(name, ok, result)| Message {
            role: Role::Tool,
            content: json!({"name":name,"ok":ok,"result":result}).to_string(),
        }));
        messages.push(Message {
            role: Role::Assistant,
            content: "new final answer".into(),
        });
        CompletedTurn::from_outcome(
            "current correction",
            &previous,
            &Outcome {
                answer: "new final answer".into(),
                rounds: 1,
                messages,
            },
        )
        .unwrap()
    }
    struct FixedExtractor(Result<Vec<String>, String>);
    impl FactExtractor for FixedExtractor {
        fn extract<'a>(
            &'a mut self,
            _: &'a CompletedTurn,
        ) -> BoxFuture<'a, Result<Vec<String>, String>> {
            Box::pin(std::future::ready(self.0.clone()))
        }
    }
    struct NeverExtractor;
    impl FactExtractor for NeverExtractor {
        fn extract<'a>(
            &'a mut self,
            _: &'a CompletedTurn,
        ) -> BoxFuture<'a, Result<Vec<String>, String>> {
            Box::pin(std::future::pending())
        }
    }
    fn evidence_count(pipeline: &MemoryPipeline) -> i64 {
        Connection::open(&pipeline.memory_db)
            .unwrap()
            .query_row("SELECT count(*) FROM evidence", [], |r| r.get(0))
            .unwrap()
    }
    #[test]
    fn prompt_matches_python_and_tool_evidence_never_contains_recall_or_failures() {
        let source = include_str!("../../../../python/mai/memory/extraction/service.py");
        let prompt = source
            .split_once("_FACT_EXTRACTION_SYSTEM = \"\"\"")
            .unwrap()
            .1
            .split_once("\"\"\".strip()")
            .unwrap()
            .0
            .trim();
        assert_eq!(EXTRACTION_SYSTEM.trim(), prompt);
        let turn = turn(vec![
            ("memory_search", true, json!("old recalled memory")),
            ("document_read", true, json!({"bounded":"new evidence"})),
            ("terminal_run", false, json!("failure")),
        ]);
        let payload = turn.extraction_payload();
        assert_eq!(
            payload["previous_assistant_message"],
            "previous approved topic"
        );
        assert_eq!(payload["assistant_final_answer"], "new final answer");
        assert_eq!(
            payload["successful_tool_results"],
            json!([json!({"bounded":"new evidence"}).to_string()])
        );
    }
    #[tokio::test]
    async fn admission_matches_python_for_recall_updates_failures_and_nonrecall() {
        for (recall, ok, facts, expected_skipped, expected_error) in [
            (true, true, Ok(vec![]), true, false),
            (true, true, Ok(vec!["사과".into()]), false, false),
            (true, true, Err("extraction failed".into()), false, true),
            (true, false, Ok(vec![]), false, false),
            (false, true, Ok(vec![]), false, false),
            (true, true, Ok(vec![" ".into()]), false, true),
        ] {
            let (_dir, pipeline) = fixture();
            let name = if recall {
                "memory_search"
            } else {
                "document_read"
            };
            let result = pipeline
                .process(
                    turn(vec![(name, ok, json!("result"))]),
                    &mut FixedExtractor(facts),
                    &CancellationToken::new(),
                )
                .await
                .unwrap();
            match result {
                MemoryUpdate::SkippedRecall => assert!(expected_skipped),
                MemoryUpdate::Stored {
                    extraction_error, ..
                } => {
                    assert!(!expected_skipped);
                    assert_eq!(extraction_error.is_some(), expected_error);
                }
            }
            assert_eq!(evidence_count(&pipeline), i64::from(!expected_skipped));
        }
    }
    #[tokio::test(start_paused = true)]
    async fn extraction_timeout_preserves_raw_evidence_with_explicit_error() {
        let (_dir, pipeline) = fixture();
        let result = pipeline
            .process(
                turn(vec![("memory_search", true, json!("past"))]),
                &mut NeverExtractor,
                &CancellationToken::new(),
            )
            .await
            .unwrap();
        assert!(matches!(
            result,
            MemoryUpdate::Stored {
                extraction_error: Some(_),
                ..
            }
        ));
        assert_eq!(evidence_count(&pipeline), 1);
    }
    #[tokio::test]
    async fn cancellation_never_turns_into_raw_evidence_admission() {
        let (_dir, pipeline) = fixture();
        let cancel = CancellationToken::new();
        cancel.cancel();
        assert!(pipeline
            .process(turn(vec![]), &mut NeverExtractor, &cancel)
            .await
            .is_err());
        assert_eq!(evidence_count(&pipeline), 0);
    }
    #[tokio::test]
    async fn stored_facts_deduplicate_and_are_immediately_visible_to_same_owner() {
        let (_dir, pipeline) = fixture();
        let result = pipeline
            .process(
                turn(vec![]),
                &mut FixedExtractor(Ok(vec!["사과".into(), " 사과 ".into()])),
                &CancellationToken::new(),
            )
            .await
            .unwrap();
        let MemoryUpdate::Stored { fact_ids, .. } = result else {
            panic!("not stored")
        };
        assert_eq!(fact_ids.len(), 1);
        let read =
            crate::native_memory::NativeMemory::new(&pipeline.memory_db, &pipeline.owner).unwrap();
        assert_eq!(
            read.search(&json!({"query":"사"})).unwrap()["nodes"][0]["node_id"],
            fact_ids[0]
        );
    }
}
