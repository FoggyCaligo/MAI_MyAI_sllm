//! Rust port of FoggyCaligo/Sentence_Breaker lang_graph.py (663dde2b).
//! Original licensed under Apache-2.0; see rust/third_party/Sentence_Breaker-LICENSE.
//! Changes: incremental path intersections, SQLite transaction supplied by caller.
use rusqlite::{params, Connection, Transaction};
use std::{collections::HashSet, path::Path};
use uuid::Uuid;

type State = (String, i64);

/// Attach an existing graph in read/write mode. Never create or migrate it.
/// An attached graph lets ordinary errors roll back memory and learning together.
pub(crate) fn attach(db: &Connection, path: &Path) -> Result<(), String> {
    let path = std::fs::canonicalize(path).map_err(|e| format!("Sentence_Breaker DB: {e}"))?;
    if !path.is_file() {
        return Err("Sentence_Breaker DB must be an existing file".into());
    }
    let mut uri =
        reqwest::Url::from_file_path(path).map_err(|_| "invalid Sentence_Breaker DB path")?;
    uri.set_query(Some("mode=rw"));
    db.execute("ATTACH DATABASE ?1 AS mai_segments", [uri.as_str()])
        .map_err(|e| e.to_string())?;
    for sql in [
        "SELECT alph FROM mai_segments.alph_nodes LIMIT 0",
        "SELECT sentence_id,char_length FROM mai_segments.sentence_layers LIMIT 0",
        "SELECT source_alph,target_alph,sentence_id,position FROM mai_segments.alph_links LIMIT 0",
    ] {
        db.prepare(sql)
            .map_err(|e| format!("unsupported Sentence_Breaker schema: {e}"))?;
    }
    Ok(())
}

#[derive(Clone, Copy, Default)]
struct Choice {
    end: usize,
    score: u64,
    supported: usize,
    pieces: usize,
}

/// Segment against history before ingesting this input, exactly as Python does.
/// The supplied transaction also covers the permanent memory writes.
pub(crate) fn segment_and_ingest(tx: &Transaction<'_>, text: &str) -> Result<Vec<String>, String> {
    let chars: Vec<_> = text.chars().collect();
    if chars.is_empty() {
        return Ok(vec![]);
    }
    let mut query = tx.prepare("SELECT sentence_id,position FROM mai_segments.alph_links WHERE source_alph=?1 AND target_alph=?2")
        .map_err(|e| e.to_string())?;
    let mut steps: Vec<HashSet<State>> = Vec::new();
    for pair in chars.windows(2) {
        steps.push(
            query
                .query_map(params![pair[0].to_string(), pair[1].to_string()], |r| {
                    Ok((r.get(0)?, r.get(1)?))
                })
                .map_err(|e| e.to_string())?
                .collect::<Result<_, _>>()
                .map_err(|e| e.to_string())?,
        );
    }
    let mut best = vec![Choice::default(); chars.len() + 1];
    for start in (0..chars.len()).rev() {
        let tail = best[start + 1];
        let mut choice = Choice {
            end: start + 1,
            score: tail.score,
            supported: tail.supported,
            pieces: tail.pieces + 1,
        };
        let mut survivors = steps.get(start).cloned().unwrap_or_default();
        for end in start + 2..=chars.len() {
            if end > start + 2 {
                let next = &steps[end - 2];
                survivors = survivors
                    .into_iter()
                    .filter_map(|(sentence, pos)| {
                        let advanced = (sentence, pos.checked_add(1)?);
                        next.contains(&advanced).then_some(advanced)
                    })
                    .collect();
            }
            if survivors.is_empty() {
                break;
            }
            let len = end - start;
            let tail = best[end];
            let candidate = Choice {
                end,
                score: tail.score + (survivors.len() as u64) * (len as u64),
                supported: tail.supported + len,
                pieces: tail.pieces + 1,
            };
            // Python: score, supported chars, first segment length, -piece count.
            let key = |c: Choice| {
                (
                    c.score,
                    c.supported,
                    c.end - start,
                    std::cmp::Reverse(c.pieces),
                )
            };
            if key(candidate) > key(choice) {
                choice = candidate;
            }
        }
        best[start] = choice;
    }
    let mut segments = Vec::new();
    let mut start = 0;
    while start < chars.len() {
        let end = best[start].end;
        segments.push(chars[start..end].iter().collect());
        start = end;
    }
    let id = format!("sentence-{}", Uuid::new_v4());
    for ch in &chars {
        tx.execute(
            "INSERT OR IGNORE INTO mai_segments.alph_nodes(alph) VALUES (?1)",
            [ch.to_string()],
        )
        .map_err(|e| e.to_string())?;
    }
    tx.execute(
        "INSERT INTO mai_segments.sentence_layers(sentence_id,char_length) VALUES (?1,?2)",
        params![id, chars.len() as i64],
    )
    .map_err(|e| e.to_string())?;
    for (pos, pair) in chars.windows(2).enumerate() {
        tx.execute("INSERT INTO mai_segments.alph_links(source_alph,target_alph,sentence_id,position) VALUES (?1,?2,?3,?4)",
            params![pair[0].to_string(),pair[1].to_string(),id,pos as i64]).map_err(|e|e.to_string())?;
    }
    Ok(segments)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;
    #[test]
    fn matches_python_reference_for_131_sequential_inputs() {
        let reference: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/sentence-breaker-parity.json"
        ))
        .unwrap();
        for sequence in reference["sequences"].as_array().unwrap() {
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("segments.sqlite3");
            Connection::open(&path)
                .unwrap()
                .execute_batch(include_str!(
                    "../tests/fixtures/sentence-breaker-schema.sql"
                ))
                .unwrap();
            let mut db = Connection::open_in_memory().unwrap();
            attach(&db, &path).unwrap();
            for step in sequence.as_array().unwrap() {
                let tx = db.transaction().unwrap();
                let actual = segment_and_ingest(&tx, step["text"].as_str().unwrap()).unwrap();
                assert_eq!(
                    serde_json::json!(actual),
                    step["segments"],
                    "input={}",
                    step["text"]
                );
                tx.commit().unwrap();
            }
        }
    }
    #[test]
    fn attach_never_creates_a_missing_graph() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("absent.sqlite3");
        assert!(attach(&Connection::open_in_memory().unwrap(), &path).is_err());
        assert!(!path.exists());
    }
}
