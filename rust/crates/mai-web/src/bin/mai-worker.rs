//! Experimental Rust remote native worker. Connects outward to an HTTPS MAI
//! server, never exposes an inbound listener. Credentials from environment only.
use mai_runtime::{
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
}
struct Worker {
    root: Workspace,
    state: std::sync::Mutex<WorkerState>,
}
fn valid_run_id(s: &str) -> bool {
    s.len() == 32 && s.as_bytes().iter().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}
impl Worker {
    fn new(path: PathBuf) -> Result<Self, String> {
        Ok(Self { root: Workspace::new(path)?, state: std::sync::Mutex::new(WorkerState::default()) })
    }
    fn lock_state(&self)->Result<std::sync::MutexGuard<'_, WorkerState>,String>{
        self.state.lock().map_err(|_|"worker run state lock poisoned".into())
    }
    fn create_run(&self,id:&str)->Result<Arc<WorkerRun>,String>{
        if !valid_run_id(id){return Err("run ID must be 32 lowercase hex characters".into());}
        let mut state=self.lock_state()?;
        if state.finished.contains(id){return Err("run is already finished; cannot recreate scratch".into());}
        if let Some(run)=state.runs.get(id){return Ok(run.clone());}
        if state.runs.len()>=16{return Err("worker active run limit reached".into());}
        let files=Arc::new(RunFiles::new(self.root.clone())?);
        let terminals=Arc::new(TerminalSessions::new(files.clone(),self.root.root().to_path_buf()));
        let run=Arc::new(WorkerRun{
            files,terminals,cancel:CancellationToken::new(),
            execution:tokio::sync::Mutex::new(()),
            closed:std::sync::atomic::AtomicBool::new(false),
        });
        state.runs.insert(id.to_string(),run.clone());
        Ok(run)
    }
    fn get_run(&self,id:&str)->Result<Option<Arc<WorkerRun>>,String>{
        Ok(self.lock_state()?.runs.get(id).cloned())
    }
    fn cancel_run(&self,id:&str)->Result<Value,String>{
        let found=self.get_run(id)?;
        if let Some(run)=&found{run.cancel.cancel();}
        Ok(json!({"cancelled":true,"already_finished":found.is_none()}))
    }
    fn cancel_all(&self){
        if let Ok(state)=self.lock_state(){
            for run in state.runs.values(){run.cancel.cancel();}
        }
    }
    async fn handle(&self,operation:&str,args:Value)->Result<Value,String>{
        let id=args.get("run_id").and_then(Value::as_str).ok_or("missing run_id")?;
        if !valid_run_id(id){return Err("invalid remote worker run ID".into());}
        match operation{
            "native.invoke"=>{
                let name=args.get("name").and_then(Value::as_str).ok_or("missing native name")?;
                if name=="switch_workspace"{return Err("switch_workspace executes only on host".into());}
                let params=args.get("arguments").ok_or("missing arguments")?;
                if !params.is_object(){return Err("native arguments must be a JSON object".into());}
                let run=self.create_run(id)?;
                // Serialize operations per run; cleanup waits for the in-flight
                // operation. The cancel control never waits on this mutex.
                let _guard=run.execution.lock().await;
                if run.closed.load(std::sync::atomic::Ordering::Acquire) || run.cancel.is_cancelled(){
                    return Err("worker run cancelled or closed".into());
                }
                let scoped=self.root.scoped_to_run(&run.files.scratch_path()?)?;
                let mut tools=Registry::new();
                register_workspace_reads(&mut tools,&scoped)?;
                register_run_file_tools(&mut tools,&run.files)?;
                register_terminal_sessions(&mut tools,run.terminals.clone(),run.cancel.clone())?;
                let tool=tools.take(name).ok_or_else(||format!("unsupported remote tool: {name}"))?;
                validate_arguments(&tool.schema.parameters,params)?;
                (tool.invoke)(params.clone()).await
            }
            "native.cancel"=>self.cancel_run(id),
            "native.cleanup"=>{
                if args.as_object().is_none_or(|o|o.len()!=1){
                    return Err("cleanup accepts only run_id".into());
                }
                let existing={
                    let mut state=self.lock_state()?;
                    state.finished.insert(id.to_string());
                    state.runs.remove(id)
                };
                if let Some(run)=existing{
                    run.closed.store(true,std::sync::atomic::Ordering::Release);
                    run.cancel.cancel();
                    let _guard=run.execution.lock().await;
                    run.terminals.close_all().await;
                    let files=run.files.clone();
                    tokio::task::spawn_blocking(move||files.cleanup())
                        .await.map_err(|e|e.to_string())??;
                }
                Ok(json!({"cleaned":true}))
            }
            _=>Err("unsupported native worker operation".into())
        }
    }
    async fn close_all(&self){
        self.cancel_all();
        let ids=match self.lock_state(){Ok(state)=>state.runs.keys().cloned().collect::<Vec<_>>(),Err(_)=>return};
        for id in ids{
            let _=self.handle("native.cleanup",json!({"run_id":id})).await;
        }
    }
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
    let mut worker = Worker::new(root)?;
    let client = Client::builder()
        .timeout(Duration::from_secs(40))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|e| e.to_string())?;
    let url = format!("{}/remote/agent/{}", base.trim_end_matches('/'), session);
    let outcome = async {
        loop {
            let polled = client
                .post(format!("{url}/poll"))
                .header("X-MAI-Agent-Secret", &secret)
                .send()
                .await
                .map_err(|e| format!("worker poll: {e}"))?;
            if polled.status() != StatusCode::OK {
                return Err(format!("worker poll rejected: {}", polled.status()));
            }
            let message: Value = polled.json().await.map_err(|e| e.to_string())?;
            let Some(item) = message.get("request").filter(|v| !v.is_null()) else {
                continue;
            };
            let id = item
                .get("request_id")
                .and_then(Value::as_str)
                .ok_or("invalid worker request ID")?;
            let operation = item
                .get("operation")
                .and_then(Value::as_str)
                .ok_or("invalid operation")?;
            let args = item
                .get("arguments")
                .cloned()
                .ok_or("missing worker arguments")?;
            let response = match worker.handle(operation, args).await {
                Ok(value) => json!({"request_id":id,"ok":true,"result":{"value":value}}),
                Err(e) => {
                    json!({"request_id":id,"ok":false,"error_type":"NativeToolError","error":e})
                }
            };
            let confirmed = client
                .post(format!("{url}/result"))
                .header("X-MAI-Agent-Secret", &secret)
                .json(&response)
                .send()
                .await
                .map_err(|e| e.to_string())?;
            if !confirmed.status().is_success() {
                return Err(format!(
                    "remote result delivery refused: {}",
                    confirmed.status()
                ));
            }
        }
        #[allow(unreachable_code)]
        Ok::<(), String>(())
    }
    .await;
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
    async fn client_persistent_bash_and_cleanup() {
        let dir = tempfile::tempdir().unwrap();
        let mut worker = Worker::new(dir.path().to_path_buf()).unwrap();
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
        assert!(worker.runs.is_empty());
    }
    #[tokio::test]
    async fn client_temporary_files_cleanup_without_touching_persistent_files() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("existing.txt"), "user-content").unwrap();
        let mut worker = Worker::new(dir.path().to_path_buf()).unwrap();
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
}
