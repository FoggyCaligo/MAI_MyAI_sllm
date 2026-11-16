//! Experimental Rust remote native worker. Connects outward to an HTTPS MAI
//! server, never exposes an inbound listener. Credentials from environment only.
use mai_runtime::{
    client_scratch::ClientScratchGuard,
    file_mutations::{register_run_file_tools, RunFiles},
    terminal_sessions::{register_terminal_sessions, TerminalSessions},
    validate_arguments,
    workspace_tools::{register_workspace_reads, Workspace},
    Registry,
};
use reqwest::{Client, StatusCode};
use serde_json::{json, Value};
use std::{collections::HashMap, env, path::PathBuf, sync::Arc, time::Duration};
use tokio_util::sync::CancellationToken;

struct WorkerRun {
    files: Arc<RunFiles>,
    terminals: Arc<TerminalSessions>,
    cancel: CancellationToken,
    execution: tokio::sync::Mutex<()>,
    closed: std::sync::atomic::AtomicBool,
}
#[derive(Default)]
struct WorkerState {
    runs: HashMap<String, Arc<WorkerRun>>,
    finished: std::collections::HashSet<String>,
    cleanup_errors: HashMap<String, String>,
}
struct Worker {
    root: Workspace,
    state: std::sync::Mutex<WorkerState>,
    _scratch_guard: ClientScratchGuard,
}
fn valid_run_id(s: &str) -> bool {
    s.len() == 32
        && s.as_bytes()
            .iter()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}
impl Worker {
    fn new(path: PathBuf) -> Result<Self, String> {
        let root = Workspace::new(path)?;
        let guard = ClientScratchGuard::acquire(&root)?;
        // No other cooperating Client worker can own this scratch while
        // recovery runs; never touch the Host's regular tmp/run-* entries.
        guard.recover_orphans()?;
        Ok(Self {
            root,
            state: std::sync::Mutex::new(WorkerState::default()),
            _scratch_guard: guard,
        })
    }
    fn lock_state(&self) -> Result<std::sync::MutexGuard<'_, WorkerState>, String> {
        self.state
            .lock()
            .map_err(|_| "worker run state lock poisoned".into())
    }
    fn create_run(&self, id: &str) -> Result<Arc<WorkerRun>, String> {
        if !valid_run_id(id) {
            return Err("run ID must be 32 lowercase hex characters".into());
        }
        let mut state = self.lock_state()?;
        if state.finished.contains(id) {
            return Err("run is already finished; cannot recreate scratch".into());
        }
        if let Some(run) = state.runs.get(id) {
            return Ok(run.clone());
        }
        if state.runs.len() >= 16 {
            return Err("worker active run limit reached".into());
        }
        let files = Arc::new(RunFiles::new_client(self.root.clone())?);
        let terminals = Arc::new(TerminalSessions::new(
            files.clone(),
            self.root.root().to_path_buf(),
        ));
        let run = Arc::new(WorkerRun {
            files,
            terminals,
            cancel: CancellationToken::new(),
            execution: tokio::sync::Mutex::new(()),
            closed: std::sync::atomic::AtomicBool::new(false),
        });
        state.runs.insert(id.to_string(), run.clone());
        Ok(run)
    }
    fn get_run(&self, id: &str) -> Result<Option<Arc<WorkerRun>>, String> {
        Ok(self.lock_state()?.runs.get(id).cloned())
    }
    fn cancel_run(&self, id: &str) -> Result<Value, String> {
        let found = self.get_run(id)?;
        if let Some(run) = &found {
            run.cancel.cancel();
        }
        Ok(json!({"cancelled":true,"already_finished":found.is_none()}))
    }
    fn cancel_all(&self) {
        if let Ok(state) = self.lock_state() {
            for run in state.runs.values() {
                run.cancel.cancel();
            }
        }
    }
    async fn handle(&self, operation: &str, args: Value) -> Result<Value, String> {
        let id = args
            .get("run_id")
            .and_then(Value::as_str)
            .ok_or("missing run_id")?;
        if !valid_run_id(id) {
            return Err("invalid remote worker run ID".into());
        }
        match operation {
            "native.invoke" => {
                let name = args
                    .get("name")
                    .and_then(Value::as_str)
                    .ok_or("missing native name")?;
                if name == "switch_workspace" {
                    return Err("switch_workspace executes only on host".into());
                }
                let params = args.get("arguments").ok_or("missing arguments")?;
                if !params.is_object() {
                    return Err("native arguments must be a JSON object".into());
                }
                let run = self.create_run(id)?;
                // Serialize operations per run; cleanup waits for the in-flight
                // operation. The cancel control never waits on this mutex.
                let _guard = run.execution.lock().await;
                if run.closed.load(std::sync::atomic::Ordering::Acquire)
                    || run.cancel.is_cancelled()
                {
                    return Err("worker run cancelled or closed".into());
                }
                let scoped = self.root.scoped_to_run(&run.files.scratch_path()?)?;
                let mut tools = Registry::new();
                register_workspace_reads(&mut tools, &scoped)?;
                register_run_file_tools(&mut tools, &run.files)?;
                register_terminal_sessions(&mut tools, run.terminals.clone(), run.cancel.clone())?;
                let tool = tools
                    .take(name)
                    .ok_or_else(|| format!("unsupported remote tool: {name}"))?;
                validate_arguments(&tool.schema.parameters, params)?;
                (tool.invoke)(params.clone()).await
            }
            "native.cancel" => self.cancel_run(id),
            "native.cleanup" => {
                if args.as_object().is_none_or(|o| o.len() != 1) {
                    return Err("cleanup accepts only run_id".into());
                }
                let existing = {
                    let mut state = self.lock_state()?;
                    if let Some(previous) = state.cleanup_errors.get(id) {
                        return Err(format!("prior scratch cleanup failed: {previous}"));
                    }
                    state.finished.insert(id.to_string());
                    state.runs.remove(id)
                };
                if let Some(run) = existing {
                    run.closed.store(true, std::sync::atomic::Ordering::Release);
                    run.cancel.cancel();
                    let _guard = run.execution.lock().await;
                    run.terminals.close_all().await;
                    let files = run.files.clone();
                    let cleanup = tokio::task::spawn_blocking(move || files.cleanup())
                        .await
                        .map_err(|e| e.to_string())
                        .and_then(|result| result);
                    if let Err(error) = cleanup {
                        self.lock_state()?
                            .cleanup_errors
                            .insert(id.to_string(), error.clone());
                        return Err(format!("scratch cleanup failed: {error}"));
                    }
                }
                Ok(json!({"cleaned":true}))
            }
            _ => Err("unsupported native worker operation".into()),
        }
    }
    async fn close_all(&self) {
        self.cancel_all();
        let ids = match self.lock_state() {
            Ok(state) => state.runs.keys().cloned().collect::<Vec<_>>(),
            Err(_) => return,
        };
        for id in ids {
            if let Err(error) = self.handle("native.cleanup", json!({"run_id":id})).await {
                eprintln!("remote worker scratch cleanup failed for run {id}: {error}");
            }
        }
    }
}
/// Per-process at-most-once dispatch for repeated identical request IDs.
/// A modified payload under an existing request ID is rejected instead of
/// executing an unexpected command. Results are retained for this session.
fn valid_request_id(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 128
        && s.bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
}
type SharedResult = Arc<tokio::sync::OnceCell<Value>>;
type RequestRecord = (String, SharedResult);
struct RequestLedger {
    entries: tokio::sync::Mutex<HashMap<String, RequestRecord>>,
    historical: std::collections::HashSet<String>,
    journal: std::sync::Mutex<std::fs::File>,
}
impl RequestLedger {
    fn open(worker: &Worker) -> Result<Self, String> {
        let path = worker._scratch_guard.root().join(".request-ids");
        let previous = match std::fs::read_to_string(&path) {
            Ok(text) => text,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => String::new(),
            Err(error) => return Err(format!("request journal read: {error}")),
        };
        if !previous.is_empty() && !previous.ends_with('\n') {
            return Err("incomplete request journal; refusing unsafe restart".into());
        }
        let mut historical = std::collections::HashSet::new();
        for line in previous.lines() {
            if !valid_request_id(line) {
                return Err("invalid request journal entry; refusing unsafe restart".into());
            }
            historical.insert(line.to_string());
        }
        if historical.len() >= 100_000 {
            return Err("request journal exhausted; rotate after manual inspection".into());
        }
        if let Ok(metadata)=std::fs::symlink_metadata(&path) {
            if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
                return Err("worker request journal must be an ordinary file".into());
            }
        }
        let journal = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .map_err(|e| format!("request journal open: {e}"))?;
        Ok(Self {
            entries: tokio::sync::Mutex::new(HashMap::new()),
            historical,
            journal: std::sync::Mutex::new(journal),
        })
    }
    async fn execute(&self, worker: &Worker, item: &Value) -> Result<Value, String> {
        let request_id = item
            .get("request_id")
            .and_then(Value::as_str)
            .ok_or("invalid request ID")?;
        if !valid_request_id(request_id) {
            return Err("invalid request ID syntax".into());
        }
        let signature = serde_json::to_string(&(item.get("operation"), item.get("arguments")))
            .map_err(|e| e.to_string())?;
        let cell = {
            let mut entries = self.entries.lock().await;
            if let Some((previous, cell)) = entries.get(request_id) {
                if previous != &signature {
                    return Err("conflicting request ID replay rejected".into());
                }
                cell.clone()
            } else {
                if entries.len() >= 4096 {
                    return Err("worker request ledger full; restart after cleanup".into());
                }
                if self.historical.contains(request_id) {
                    return Err(
                        "request ID existed before worker restart; refusing re-execution".into(),
                    );
                }
                if self.historical.len() + entries.len() >= 100_000 {
                    return Err("worker persistent request ledger limit reached".into());
                }
                // Write-ahead record: a crash after this point can NEVER
                // cause an unknown prior command to execute again.
                {
                    use std::io::Write;
                    let mut journal = self
                        .journal
                        .lock()
                        .map_err(|_| "request journal lock poisoned")?;
                    writeln!(journal, "{request_id}")
                        .map_err(|e| format!("request journal write: {e}"))?;
                    journal
                        .sync_data()
                        .map_err(|e| format!("request journal sync: {e}"))?;
                }
                let cell = Arc::new(tokio::sync::OnceCell::new());
                entries.insert(request_id.to_string(), (signature, cell.clone()));
                cell
            }
        };
        let response = cell
            .get_or_init(|| async {
                let operation = item.get("operation").and_then(Value::as_str).unwrap_or("");
                let args = item.get("arguments").cloned().unwrap_or(Value::Null);
                match worker.handle(operation, args).await {
                    Ok(value) => {
                        json!({"request_id":request_id,"ok":true,"result":{"value":value}})
                    }
                    Err(error) => json!({"request_id":request_id,"ok":false,
                    "error_type":"NativeToolError","error":error}),
                }
            })
            .await;
        Ok(response.clone())
    }
}
async fn deliver_result(
    client: Client,
    url: String,
    secret: String,
    worker: Arc<Worker>,
    ledger: Arc<RequestLedger>,
    item: Value,
) -> Result<(), String> {
    let response = ledger.execute(&worker, &item).await?;
    let confirmed = client
        .post(format!("{url}/result"))
        .header("X-MAI-Agent-Secret", &secret)
        .json(&response)
        .send()
        .await
        .map_err(|e| format!("worker result delivery: {e}"))?;
    if !confirmed.status().is_success() {
        return Err(format!(
            "remote result delivery refused: {}",
            confirmed.status()
        ));
    }
    Ok(())
}

/// Reconnect is safe only when no tool execution is in flight and no
/// unfinished run owns files or shells. Never retry an ambiguous invocation.
async fn poll_with_idle_reconnect(
    client: &Client,
    url: &str,
    secret: &str,
    worker: &Worker,
    has_inflight: bool,
) -> Result<Value, String> {
    for attempt in 0..4u64 {
        let response = client
            .post(format!("{url}/poll"))
            .header("X-MAI-Agent-Secret", secret)
            .send()
            .await;
        let message = match response {
            Ok(reply) if reply.status() == StatusCode::OK => {
                return reply.json::<Value>().await.map_err(|e| e.to_string());
            }
            Ok(reply)
                if reply.status() == StatusCode::UNAUTHORIZED
                    || reply.status() == StatusCode::FORBIDDEN =>
            {
                return Err(format!("remote session revoked: {}", reply.status()));
            }
            Ok(reply) => format!("worker poll server error: {}", reply.status()),
            Err(error) => format!("worker poll transport error: {error}"),
        };
        let safe = worker.lock_state()?.runs.is_empty() && !has_inflight;
        if !safe {
            return Err(format!(
                "{message}; active run cancelled to avoid ambiguous replay"
            ));
        }
        if attempt == 3 {
            return Err(format!("{message}; reconnect attempts exhausted"));
        }
        tokio::time::sleep(Duration::from_millis(200 * (1 << attempt))).await;
    }
    Err("remote session reconnect exhausted".into())
}

async fn run_worker() -> Result<(), String> {
    let base = env::var("MAI_REMOTE_SERVER").map_err(|_| "MAI_REMOTE_SERVER required")?;
    if !base.to_ascii_lowercase().starts_with("https://") {
        return Err("Remote Rust worker requires HTTPS".into());
    }
    let session = env::var("MAI_REMOTE_SESSION").map_err(|_| "MAI_REMOTE_SESSION required")?;
    let secret = env::var("MAI_REMOTE_KEY").map_err(|_| "MAI_REMOTE_KEY required")?;
    if session.trim().is_empty() || secret.trim().is_empty() {
        return Err("remote credentials must be nonempty".into());
    }
    let root = env::var("MAI_CLIENT_WORKSPACE_ROOT")
        .map(PathBuf::from)
        .map_err(|_| "MAI_CLIENT_WORKSPACE_ROOT required")?;
    let worker = Arc::new(Worker::new(root)?);
    let client = Client::builder()
        .timeout(Duration::from_secs(40))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|e| e.to_string())?;
    let url = format!("{}/remote/agent/{}", base.trim_end_matches('/'), session);
    let ledger = Arc::new(RequestLedger::open(&worker)?);
    let mut tasks = tokio::task::JoinSet::<Result<(), String>>::new();
    let outcome = tokio::select! {
        result = async {
        loop {
            // Completed tool responses must reach the server. Any failed
            // delivery indicates a disconnected/revoked session: fail closed.
            while let Some(joined) = tasks.try_join_next() {
                joined.map_err(|e| format!("remote worker operation task: {e}"))??;
            }
            // Polling continues even while an earlier native.invoke is
            // executing, so native.cancel can reach the same worker promptly.
            let message=poll_with_idle_reconnect(
                &client,&url,&secret,&worker,!tasks.is_empty()
            ).await?;
            let Some(item) = message.get("request").filter(|v| !v.is_null()) else {
                continue;
            };
            let operation = item
                .get("operation")
                .and_then(Value::as_str)
                .ok_or("invalid operation")?;
            let args = item.get("arguments").ok_or("missing worker arguments")?;
            if operation == "native.invoke" {
                let run_id = args
                    .get("run_id")
                    .and_then(Value::as_str)
                    .ok_or("missing run ID")?;
                // Pre-create the run in dispatch order before spawning.
                // Later cleanup cannot overtake the initial run creation.
                worker.create_run(run_id)?;
            }
            tasks.spawn(deliver_result(
                client.clone(),
                url.clone(),
                secret.clone(),
                worker.clone(),
                ledger.clone(),
                item.clone(),
            ));
        }
        #[allow(unreachable_code)]
        Ok::<(), String>(())
        } => result,
        signal = tokio::signal::ctrl_c() => {
            signal.map_err(|e|format!("worker interrupt handler: {e}"))
        },
    };
    // Disconnect/revocation: cancel first, so running terminal tools observe
    // cancellation even before their ordinary result can be delivered.
    worker.cancel_all();
    let drained = tokio::time::timeout(Duration::from_secs(20), async {
        while let Some(done) = tasks.join_next().await {
            let _ = done;
        }
    })
    .await;
    if drained.is_err() {
        tasks.abort_all();
        while tasks.join_next().await.is_some() {}
    }
    worker.close_all().await;
    let _ = client
        .delete(url)
        .header("X-MAI-Agent-Secret", &secret)
        .send()
        .await;
    outcome
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    run_worker().await.map_err(std::io::Error::other)?;
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn valid_worker_scratch_identifiers_only() {
        assert!(valid_run_id("0123456789abcdef0123456789abcdef"));
        assert!(!valid_run_id("abc"));
        assert!(!valid_run_id("01234567-89ab-cdef-0123-456789abcdef"));
        assert!(!valid_run_id("0123456789ABCDEF0123456789abcdef"));
    }
    #[tokio::test]
    async fn repeated_request_id_never_repeats_file_mutation() {
        let dir = tempfile::tempdir().unwrap();
        let worker = Worker::new(dir.path().to_path_buf()).unwrap();
        let ledger = RequestLedger::open(&worker).unwrap();
        let run_id = "0123456789abcdef0123456789abcdef";
        let item = json!({"request_id":"fixed-1","operation":"native.invoke",
            "arguments":{"run_id":run_id,"name":"file_write",
                "arguments":{"path":"once.txt","content":"first","lifecycle":"persistent"}}});
        let first = ledger.execute(&worker, &item).await.unwrap();
        assert_eq!(first["ok"], true);
        let second = ledger.execute(&worker, &item).await.unwrap();
        assert_eq!(first, second);
        assert_eq!(
            std::fs::read_to_string(dir.path().join("once.txt")).unwrap(),
            "first"
        );
        let conflicting = json!({"request_id":"fixed-1","operation":"native.invoke",
            "arguments":{"run_id":run_id,"name":"file_write",
                "arguments":{"path":"once.txt","content":"changed","lifecycle":"persistent"}}});
        assert!(ledger
            .execute(&worker, &conflicting)
            .await
            .unwrap_err()
            .contains("conflicting"));
        assert_eq!(
            std::fs::read_to_string(dir.path().join("once.txt")).unwrap(),
            "first"
        );
        worker.close_all().await;
    }
    #[tokio::test]
    async fn concurrent_same_request_id_coalesces_into_one_run() {
        let dir = tempfile::tempdir().unwrap();
        let worker = Arc::new(Worker::new(dir.path().to_path_buf()).unwrap());
        let ledger = Arc::new(RequestLedger::open(&worker).unwrap());
        let run_id = "0123456789abcdef0123456789abcdef";
        let item = json!({"request_id":"same-2","operation":"native.invoke",
            "arguments":{"run_id":run_id,"name":"file_write",
                "arguments":{"path":"only-once.txt","content":"success","lifecycle":"persistent"}}});
        let a = ledger.execute(&worker, &item);
        let b = ledger.execute(&worker, &item);
        let (one, two) = tokio::join!(a, b);
        assert_eq!(one.unwrap(), two.unwrap());
        assert_eq!(ledger.entries.lock().await.len(), 1);
        worker.close_all().await;
    }
    #[tokio::test]
    async fn transient_poll_failure_reconnects_only_when_worker_is_idle() {
        use axum::{routing::post, Json, Router};
        use std::sync::atomic::{AtomicUsize, Ordering};
        let hits = Arc::new(AtomicUsize::new(0));
        let count = hits.clone();
        let router = Router::new().route(
            "/poll",
            post(move || {
                let count = count.clone();
                async move {
                    let attempt = count.fetch_add(1, Ordering::SeqCst);
                    if attempt < 2 {
                        (
                            axum::http::StatusCode::SERVICE_UNAVAILABLE,
                            Json(json!({"error":"transient"})),
                        )
                    } else {
                        (axum::http::StatusCode::OK, Json(json!({"request":null})))
                    }
                }
            }),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            axum::serve(listener, router).await.unwrap();
        });
        let tmp = tempfile::tempdir().unwrap();
        let worker = Worker::new(tmp.path().to_path_buf()).unwrap();
        let client = Client::builder()
            .timeout(Duration::from_secs(3))
            .build()
            .unwrap();
        let url = format!("http://{addr}");
        let result = poll_with_idle_reconnect(&client, &url, "test-key", &worker, false)
            .await
            .unwrap();
        assert!(result["request"].is_null());
        assert_eq!(hits.load(Ordering::SeqCst), 3);
        worker
            .create_run("0123456789abcdef0123456789abcdef")
            .unwrap();
        let denied = poll_with_idle_reconnect(&client, "http://127.0.0.1:1", "key", &worker, false)
            .await
            .unwrap_err();
        assert!(denied.contains("active run cancelled"), "{denied}");
        worker.close_all().await;
        server.abort();
    }
    #[test]
    fn unfinished_run_prevents_reconnect_even_if_it_has_no_active_tool() {
        let dir = tempfile::tempdir().unwrap();
        let worker = Worker::new(dir.path().to_path_buf()).unwrap();
        let id = "0123456789abcdef0123456789abcdef";
        assert!(worker.lock_state().unwrap().runs.is_empty());
        worker.create_run(id).unwrap();
        assert!(!worker.lock_state().unwrap().runs.is_empty());
    }
    #[tokio::test]
    async fn durable_journal_refuses_replay_after_worker_restart() {
        let tmp = tempfile::tempdir().unwrap();
        let id = "0123456789abcdef0123456789abcdef";
        let item = json!({"request_id":"replay-guard-001","operation":"native.invoke",
            "arguments":{"run_id":id,"name":"file_write",
                "arguments":{"path":"persistent.txt","content":"first","lifecycle":"persistent"}}});
        {
            let worker = Worker::new(tmp.path().to_path_buf()).unwrap();
            let ledger = RequestLedger::open(&worker).unwrap();
            assert_eq!(ledger.execute(&worker, &item).await.unwrap()["ok"], true);
            worker.close_all().await;
        }
        {
            let worker = Worker::new(tmp.path().to_path_buf()).unwrap();
            let ledger = RequestLedger::open(&worker).unwrap();
            let error = ledger.execute(&worker, &item).await.unwrap_err();
            assert!(error.contains("before worker restart"), "{error}");
            assert_eq!(
                std::fs::read_to_string(tmp.path().join("persistent.txt")).unwrap(),
                "first"
            );
        }
    }
    #[test]
    fn incomplete_journal_blocks_unsafe_automatic_recovery() {
        let tmp = tempfile::tempdir().unwrap();
        let worker = Worker::new(tmp.path().to_path_buf()).unwrap();
        std::fs::write(
            worker._scratch_guard.root().join(".request-ids"),
            "not-flushed",
        )
        .unwrap();
        assert!(RequestLedger::open(&worker).is_err());
    }
    #[test]
    fn worker_restart_reclaims_orphans_without_touching_host_scratch() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let host = root.join("tmp/run-HOST12");
        std::fs::create_dir_all(&host).unwrap();
        std::fs::write(host.join("keep.txt"), "host").unwrap();
        let abandoned = root.join("tmp/client-worker/run-ZY1234");
        std::fs::create_dir_all(&abandoned).unwrap();
        std::fs::write(abandoned.join("leftover.txt"), "old").unwrap();
        let first = Worker::new(root.to_path_buf()).unwrap();
        assert!(!abandoned.exists());
        assert!(host.join("keep.txt").exists());
        assert!(Worker::new(root.to_path_buf()).is_err());
        drop(first);
        assert!(Worker::new(root.to_path_buf()).is_ok());
    }
    #[tokio::test]
    async fn client_persistent_bash_and_cleanup() {
        let dir = tempfile::tempdir().unwrap();
        let worker = Worker::new(dir.path().to_path_buf()).unwrap();
        let run_id = "0123456789abcdef0123456789abcdef";
        let result = worker
            .handle(
                "native.invoke",
                json!({"run_id":run_id,"name":"terminal_run",
            "arguments":{"command":"export CLIENT_FLAG=still_here"}}),
            )
            .await
            .unwrap();
        assert_eq!(result["returncode"], 0);
        let second = worker
            .handle(
                "native.invoke",
                json!({"run_id":run_id,"name":"terminal_run",
            "arguments":{"command":"printf '%s' \"$CLIENT_FLAG\""}}),
            )
            .await
            .unwrap();
        assert_eq!(second["stdout"], "still_here");
        let cleaned = worker
            .handle("native.cleanup", json!({"run_id":run_id}))
            .await
            .unwrap();
        assert_eq!(cleaned["cleaned"], true);
        assert!(worker.lock_state().unwrap().runs.is_empty());
    }
    #[tokio::test]
    async fn client_temporary_files_cleanup_without_touching_persistent_files() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("existing.txt"), "user-content").unwrap();
        let worker = Worker::new(dir.path().to_path_buf()).unwrap();
        let run_id = "0123456789abcdef0123456789abcdef";
        let created = worker
            .handle(
                "native.invoke",
                json!({"run_id":run_id,"name":"file_write",
            "arguments":{"path":"scripts/temporary.txt","content":"scratch",
                "lifecycle":"temporary"}}),
            )
            .await
            .unwrap();
        let path = std::path::PathBuf::from(created["path"].as_str().unwrap());
        assert!(path.exists());
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "scratch");
        let outside = worker
            .handle(
                "native.invoke",
                json!({"run_id":run_id,"name":"file_write",
            "arguments":{"path":"permanent.txt","content":"saved","lifecycle":"persistent"}}),
            )
            .await
            .unwrap();
        assert_eq!(outside["lifecycle"], "persistent");
        worker
            .handle("native.cleanup", json!({"run_id":run_id}))
            .await
            .unwrap();
        assert!(!path.exists());
        assert_eq!(
            std::fs::read_to_string(dir.path().join("existing.txt")).unwrap(),
            "user-content"
        );
        assert_eq!(
            std::fs::read_to_string(dir.path().join("permanent.txt")).unwrap(),
            "saved"
        );
    }
    #[tokio::test]
    async fn cancellation_control_interrupts_active_client_terminal() {
        let dir = tempfile::tempdir().unwrap();
        let worker = Arc::new(Worker::new(dir.path().to_path_buf()).unwrap());
        let id = "0123456789abcdef0123456789abcdef";
        let active = worker.clone();
        let task = tokio::spawn(async move {
            active
                .handle(
                    "native.invoke",
                    json!({"run_id":id,"name":"terminal_run",
                "arguments":{"command":"sleep 8"}}),
                )
                .await
        });
        // Wait for the initial run to exist without assuming command timings.
        for _ in 0..100 {
            if worker.get_run(id).unwrap().is_some() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert!(worker.get_run(id).unwrap().is_some());
        let cancelled = tokio::time::timeout(
            Duration::from_millis(800),
            worker.handle("native.cancel", json!({"run_id":id})),
        )
        .await
        .unwrap()
        .unwrap();
        assert_eq!(cancelled["cancelled"], true);
        assert!(tokio::time::timeout(Duration::from_secs(4), task)
            .await
            .expect("cancel must interrupt active command")
            .unwrap()
            .is_err());
        let finished = worker
            .handle("native.cleanup", json!({"run_id":id}))
            .await
            .unwrap();
        assert_eq!(finished["cleaned"], true);
        assert!(worker.get_run(id).unwrap().is_none());
        assert!(worker
            .handle(
                "native.invoke",
                json!({"run_id":id,"name":"terminal_run",
            "arguments":{"command":"echo should-not-run"}})
            )
            .await
            .is_err());
    }
    #[tokio::test]
    async fn failed_cleanup_never_gets_a_false_success_ack() {
        let tmp = tempfile::tempdir().unwrap();
        let worker = Worker::new(tmp.path().to_path_buf()).unwrap();
        let id = "0123456789abcdef0123456789abcdef";
        worker
            .lock_state()
            .unwrap()
            .cleanup_errors
            .insert(id.into(), "simulated filesystem refusal".into());
        let result = worker
            .handle("native.cleanup", json!({"run_id":id}))
            .await
            .unwrap_err();
        assert!(result.contains("prior scratch cleanup failed"));
    }
    #[tokio::test]
    async fn disconnect_cancels_all_runs_and_preserves_other_files() {
        let dir = tempfile::tempdir().unwrap();
        let worker = Arc::new(Worker::new(dir.path().to_path_buf()).unwrap());
        std::fs::write(dir.path().join("user.txt"), "preserved").unwrap();
        let id = "0123456789abcdef0123456789abcdef";
        let made = worker
            .handle(
                "native.invoke",
                json!({"run_id":id,"name":"file_write",
            "arguments":{"path":"artifact.txt","content":"scratch","lifecycle":"temporary"}}),
            )
            .await
            .unwrap();
        let path = PathBuf::from(made["path"].as_str().unwrap());
        assert!(path.exists());
        worker.cancel_all();
        worker.close_all().await;
        assert!(!path.exists());
        assert_eq!(
            std::fs::read_to_string(dir.path().join("user.txt")).unwrap(),
            "preserved"
        );
    }
}
