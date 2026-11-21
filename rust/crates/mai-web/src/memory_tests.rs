use super::*;
use mai_runtime::supervisor::RamProbe;
use rusqlite::Connection;
use tokio::sync::{Notify, Semaphore};

struct FixedRam;
impl RamProbe for FixedRam {
    fn available_bytes(&self) -> Option<u64> {
        Some(16 * 1024 * 1024 * 1024)
    }
}
struct FakeModel {
    entered: Notify,
    release: Semaphore,
    calls: Mutex<Vec<Value>>,
    fact_response: String,
}
async fn models() -> Json<Value> {
    Json(json!({"data":[{"id":"test","status":{"value":"loaded"}}]}))
}
async fn chat(State(state): State<Arc<FakeModel>>, Json(body): Json<Value>) -> Json<Value> {
    state.calls.lock().await.push(body.clone());
    let content = if body.get("response_format").is_some() {
        state.entered.notify_one();
        state.release.acquire().await.unwrap().forget();
        state.fact_response.clone()
    } else {
        "answer ready".into()
    };
    Json(
        json!({"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":content}}]}),
    )
}
fn config() -> Config {
    Config {
        model: "test".into(),
        llama_url: "http://127.0.0.1:0".into(),
        ui_dir: PathBuf::from("."),
        workspace_root: None,
        enable_terminal: false,
        memory_db: None,
        segment_db: None,
        memory_write: false,
    }
}
fn owner() -> User {
    User {
        user_id: "login-owner".into(),
        user_pw: "secret".into(),
        db_id: "stable-owner".into(),
    }
}

async fn fixture(
    write: bool,
    fact_response: &str,
) -> (
    tempfile::TempDir,
    App,
    Arc<FakeModel>,
    tokio::task::JoinHandle<()>,
) {
    let dir = tempfile::tempdir().unwrap();
    let memory = dir.path().join("memory.sqlite3");
    let db = Connection::open(&memory).unwrap();
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
        INSERT INTO nodes VALUES(1,'anchor:user:stable-owner','anchor','user_anchor::stable-owner','{\"user_id\":\"stable-owner\"}',1,'t','t');
        INSERT INTO user_anchors VALUES ('stable-owner',1);").unwrap();
    let segments = dir.path().join("segments.sqlite3");
    Connection::open(&segments)
        .unwrap()
        .execute_batch(include_str!(
            "../../mai-runtime/tests/fixtures/sentence-breaker-schema.sql"
        ))
        .unwrap();
    let fake = Arc::new(FakeModel {
        entered: Notify::new(),
        release: Semaphore::new(0),
        calls: Mutex::new(vec![]),
        fact_response: fact_response.into(),
    });
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let host = format!("http://{}", listener.local_addr().unwrap());
    let router = Router::new()
        .route("/models", get(models))
        .route("/v1/chat/completions", post(chat))
        .with_state(fake.clone());
    let server = tokio::spawn(async move {
        axum::serve(listener, router).await.unwrap();
    });
    let mut cfg = config();
    cfg.llama_url = host.clone();
    cfg.memory_db = Some(memory);
    cfg.segment_db = Some(segments);
    cfg.memory_write = write;
    let mut app = App::new(cfg, vec![owner()]).unwrap();
    app.supervisor = Arc::new(ModelSupervisor::new(Policy::new(host), Arc::new(FixedRam)).unwrap());
    app.data
        .lock()
        .await
        .sessions
        .insert("auth".into(), "login-owner".into());
    (dir, app, fake, server)
}
async fn submit(app: &App) -> String {
    let mut headers = HeaderMap::new();
    headers.insert("authorization", "Bearer auth".parse().unwrap());
    let (status, Json(reply)) = submit_job(
        State(app.clone()),
        headers,
        Json(serde_json::from_value(json!({"message":"새 계획","session_id":"test"})).unwrap()),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    reply["job_id"].as_str().unwrap().into()
}
async fn wait_for(app: &App, id: &str, postprocess: bool) -> Value {
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            let data = app.data.lock().await;
            let job = &data.jobs[id];
            let done = if postprocess {
                job.memory_update
                    .as_ref()
                    .is_some_and(|x| x["status"] != "pending")
            } else {
                job.status == "completed"
            };
            if done {
                return job.snapshot(id);
            }
            drop(data);
            tokio::task::yield_now().await;
        }
    })
    .await
    .expect("job should finish")
}
#[tokio::test]
async fn answer_is_visible_before_memory_and_next_owner_turn_waits_for_storage() {
    let (_dir, app, fake, server) = fixture(true, r#"{"facts":["사과"]}"#).await;
    let first = submit(&app).await;
    tokio::time::timeout(Duration::from_secs(5), fake.entered.notified())
        .await
        .unwrap();
    let visible = wait_for(&app, &first, false).await;
    assert_eq!(visible["response"]["answer"], "answer ready");
    assert_eq!(visible["memory_update"]["status"], "pending");
    let second = submit(&app).await;
    tokio::task::yield_now().await;
    assert_eq!(app.data.lock().await.jobs[&second].status, "pending");
    assert_eq!(fake.calls.lock().await.len(), 2); // first answer + blocked extraction
    fake.release.add_permits(2);
    let stored = wait_for(&app, &first, true).await;
    assert_eq!(stored["memory_update"]["status"], "stored");
    let stored_second = wait_for(&app, &second, true).await;
    assert_eq!(stored_second["memory_update"]["status"], "stored");
    let calls = fake.calls.lock().await;
    let extractions: Vec<_> = calls
        .iter()
        .filter(|call| call.get("response_format").is_some())
        .collect();
    let payload: Value =
        serde_json::from_str(extractions[1]["messages"][1]["content"].as_str().unwrap()).unwrap();
    assert_eq!(payload["previous_assistant_message"], "answer ready");
    drop(calls);
    let reader = NativeMemory::new(app.config.memory_db.as_ref().unwrap(), "stable-owner").unwrap();
    assert_eq!(
        reader.search(&json!({"limit":50})).unwrap()["memories"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert!(
        NativeMemory::new(app.config.memory_db.as_ref().unwrap(), "login-owner")
            .unwrap()
            .search(&json!({"limit":50}))
            .unwrap()["memories"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert_eq!(app.data.lock().await.jobs[&first].status, "completed");
    server.abort();
}
#[tokio::test]
async fn invalid_extraction_preserves_answer_and_raw_evidence_with_diagnostic() {
    let (_dir, app, fake, server) = fixture(true, "invalid JSON").await;
    fake.release.add_permits(1);
    let id = submit(&app).await;
    let result = wait_for(&app, &id, true).await;
    assert_eq!(result["status"], "completed");
    assert_eq!(result["response"]["answer"], "answer ready");
    assert_eq!(result["memory_update"]["status"], "stored");
    assert!(result["memory_update"]["extraction_error"].is_string());
    let count: i64 = Connection::open(app.config.memory_db.as_ref().unwrap())
        .unwrap()
        .query_row("SELECT count(*) FROM evidence", [], |r| r.get(0))
        .unwrap();
    assert_eq!(count, 1);
    server.abort();
}
#[tokio::test]
async fn read_only_default_never_requests_extraction_or_writes_evidence() {
    let (_dir, app, fake, server) = fixture(false, r#"{"facts":["사과"]}"#).await;
    let id = submit(&app).await;
    let result = wait_for(&app, &id, false).await;
    assert!(result["memory_update"].is_null());
    assert_eq!(fake.calls.lock().await.len(), 1);
    let count: i64 = Connection::open(app.config.memory_db.as_ref().unwrap())
        .unwrap()
        .query_row("SELECT count(*) FROM evidence", [], |r| r.get(0))
        .unwrap();
    assert_eq!(count, 0);
    server.abort();
}
#[test]
fn stable_owner_mapping_and_write_opt_in_fail_closed() {
    assert!(!memory_write_enabled(None).unwrap());
    assert!(memory_write_enabled(Some("1")).unwrap());
    assert!(memory_write_enabled(Some("typo")).is_err());
    let app = App::new(config(), vec![owner()]).unwrap();
    assert_eq!(app.memory_user("login-owner").unwrap(), "stable-owner");
    assert!(app.memory_user("stable-owner").is_err());
    let mut cfg = config();
    cfg.memory_write = true;
    assert!(App::new(cfg, vec![owner()]).is_err());
    assert!(App::new(config(), vec![owner(), owner()]).is_err());
    let mut colliding = owner();
    colliding.user_id = "stable-owner".into();
    colliding.db_id = "another".into();
    assert!(App::new(config(), vec![owner(), colliding]).is_err());
}

#[tokio::test]
async fn persistence_failure_is_visible_without_losing_the_answer_or_partially_learning() {
    let (_dir, app, fake, server) = fixture(true, r#"{"facts":["사과"]}"#).await;
    Connection::open(app.config.memory_db.as_ref().unwrap())
        .unwrap()
        .execute("INSERT INTO memory_concept_exact VALUES (999,'사')", [])
        .unwrap();
    fake.release.add_permits(1);
    let id = submit(&app).await;
    let result = wait_for(&app, &id, true).await;
    assert_eq!(result["status"], "completed");
    assert_eq!(result["response"]["answer"], "answer ready");
    assert_eq!(result["memory_update"]["status"], "failed");
    assert!(result["memory_update"]["error"].is_string());
    for (path, table) in [
        (app.config.memory_db.as_ref().unwrap(), "evidence"),
        (app.config.segment_db.as_ref().unwrap(), "sentence_layers"),
    ] {
        let count: i64 = Connection::open(path)
            .unwrap()
            .query_row(&format!("SELECT count(*) FROM {table}"), [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 0);
    }
    server.abort();
}
