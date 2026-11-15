//! Run-scoped persistent Bash shells. Session state survives tool calls, but
//! never survives the owning agent run. Remote client transport is not wired.
use crate::file_mutations::RunFiles;
use crate::{Registry, Tool, ToolSchema};
use serde_json::{json, Value};
use std::{collections::HashMap, path::PathBuf, process::Stdio, sync::Arc, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStderr, ChildStdout, Command},
    sync::Mutex,
};
use tokio_util::sync::CancellationToken;

const MAX_HOST_SESSIONS: usize = 2;
const MAX_OUTPUT_BYTES: usize = 1_048_576;
const COMMAND_TIMEOUT: Duration = Duration::from_secs(180);
const MAX_COMMAND_CHARS: usize = 32768;

struct BashSession {
    child: Child,
    stdout: BufReader<ChildStdout>,
    stderr: BufReader<ChildStderr>,
    calls: u64,
}
impl BashSession {
    fn close(&mut self) {
        if let Some(pid) = self.child.id() {
            #[cfg(windows)]
            {
                let _ = std::process::Command::new("taskkill.exe")
                    .args(["/PID", &pid.to_string(), "/T", "/F"])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
            }
            #[cfg(unix)]
            if pid <= i32::MAX as u32 {
                unsafe {
                    libc::kill(-(pid as i32), libc::SIGKILL);
                }
            }
        }
        let _ = self.child.start_kill();
    }
}
impl Drop for BashSession {
    fn drop(&mut self) {
        self.close();
    }
}
pub struct TerminalSessions {
    run: Arc<RunFiles>,
    root: PathBuf,
    sessions: Mutex<HashMap<String, Arc<Mutex<BashSession>>>>,
}
impl TerminalSessions {
    pub fn new(run: Arc<RunFiles>, root: PathBuf) -> Self {
        Self {
            run,
            root,
            sessions: Mutex::new(HashMap::new()),
        }
    }
    pub async fn close_all(&self) {
        let mut slots = self.sessions.lock().await;
        slots.clear();
    }
    async fn get_or_create(&self, id: &str) -> Result<Arc<Mutex<BashSession>>, String> {
        if id.trim().is_empty()
            || id.len() > 64
            || !id
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        {
            return Err("invalid persistent terminal session ID".into());
        }
        let mut slots = self.sessions.lock().await;
        if let Some(session) = slots.get(id) {
            return Ok(session.clone());
        }
        if slots.len() >= MAX_HOST_SESSIONS {
            return Err("host terminal session limit reached (2)".into());
        }
        let scratch = self.run.scratch_path()?;
        let mut command = Command::new(bash_executable()?);
        command
            .args(["--noprofile", "--norc"])
            .current_dir(&self.root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.as_std_mut().process_group(0);
        }
        let mut child = command
            .spawn()
            .map_err(|e| format!("persistent Bash spawn: {e}"))?;
        let stdout = child
            .stdout
            .take()
            .ok_or("persistent Bash stdout unavailable")?;
        let stderr = child
            .stderr
            .take()
            .ok_or("persistent Bash stderr unavailable")?;
        let session = Arc::new(Mutex::new(BashSession {
            child,
            stdout: BufReader::new(stdout),
            stderr: BufReader::new(stderr),
            calls: 0,
        }));
        // Check scratch exists before publishing new long-lived shell.
        if !scratch.exists() {
            return Err("terminal run scratch vanished".into());
        }
        slots.insert(id.to_string(), session.clone());
        Ok(session)
    }
    pub async fn run(&self, args: Value, cancellation: CancellationToken) -> Result<Value, String> {
        if cancellation.is_cancelled() {
            return Err("terminal cancelled".into());
        }
        let command = args
            .get("command")
            .and_then(Value::as_str)
            .ok_or("missing command")?;
        if command.trim().is_empty() || command.chars().count() > MAX_COMMAND_CHARS {
            return Err("terminal command must be 1..32768 characters".into());
        }
        if args.get("shell").and_then(Value::as_str).unwrap_or("bash") != "bash" {
            return Err("persistent sessions currently support shell=bash only".into());
        }
        if args
            .get("temporary_outputs")
            .is_some_and(|v| v.as_array().is_some_and(|a| !a.is_empty()))
        {
            return Err(
                "persistent terminal temporary_outputs must use file_write lifecycle=temporary"
                    .into(),
            );
        }
        if args.get("cwd").is_some() {
            return Err(
                "persistent Bash uses initial workspace cwd; change directories with cd".into(),
            );
        }
        let id = args
            .get("session_id")
            .and_then(Value::as_str)
            .unwrap_or("default");
        if command.trim() == "exit" {
            let mut slots = self.sessions.lock().await;
            if let Some(session) = slots.remove(id) {
                // Do not hold the collection lock while waiting for the session.
                drop(slots);
                session.lock().await.close();
                return Ok(json!({"session_id":id,"closed":true}));
            }
            return Err("terminal session does not exist".into());
        }
        let session = self.get_or_create(id).await?;
        let mut shell = tokio::select! {
            _=cancellation.cancelled()=>return Err("terminal cancelled".into()),
            guard=session.lock()=>guard
        };
        shell.calls = shell
            .calls
            .checked_add(1)
            .ok_or("terminal call counter overflow")?;
        let token = format!("MAI_{}_{}", id, shell.calls);
        let suffix=format!("\n__mai_shell_rc=$?\nprintf '\\036{}:%d\\037' \"$__mai_shell_rc\"\nprintf '\\036{}\\037' >&2\n",token,token);
        let script = format!("{command}{suffix}");
        let result = tokio::time::timeout(COMMAND_TIMEOUT, async {
            let stdin = shell
                .child
                .stdin
                .as_mut()
                .ok_or("persistent Bash stdin closed")?;
            stdin
                .write_all(script.as_bytes())
                .await
                .map_err(|e| e.to_string())?;
            stdin.flush().await.map_err(|e| e.to_string())?;
            collect(&mut shell, &token, &cancellation).await
        })
        .await;
        let parsed = match result {
            Ok(value) => value,
            Err(_) => Err("persistent terminal timed out; process tree terminated".into()),
        };
        if parsed.is_err() || cancellation.is_cancelled() {
            shell.close();
            drop(shell);
            self.sessions.lock().await.remove(id);
        }
        let mut output = parsed?;
        output["session_id"] = json!(id);
        output["command"] = json!(command);
        if output["returncode"].as_i64() != Some(0) {
            return Err(format!("persistent Bash command failed: {output}"));
        }
        Ok(output)
    }
}
#[cfg(windows)]
fn bash_executable() -> Result<PathBuf, String> {
    if let Some(value) = std::env::var_os("MAI_GIT_BASH_PATH") {
        let path = PathBuf::from(value);
        if path.is_file() {
            return Ok(path);
        }
        return Err("MAI_GIT_BASH_PATH not a file".into());
    }
    for key in ["ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"] {
        if let Some(root) = std::env::var_os(key) {
            let root = PathBuf::from(root);
            let path = if key == "LOCALAPPDATA" {
                root.join("Programs")
                    .join("Git")
                    .join("bin")
                    .join("bash.exe")
            } else {
                root.join("Git").join("bin").join("bash.exe")
            };
            if path.is_file() {
                return Ok(path);
            }
        }
    }
    Err("Git Bash not found; configure MAI_GIT_BASH_PATH".into())
}
#[cfg(not(windows))]
fn bash_executable() -> Result<PathBuf, String> {
    Ok(PathBuf::from("bash"))
}

async fn collect(
    shell: &mut BashSession,
    token: &str,
    cancel: &CancellationToken,
) -> Result<Value, String> {
    let out_marker = format!("\u{1e}{token}:");
    let err_marker = format!("\u{1e}{token}\u{1f}");
    let mut stdout = Vec::new();
    let mut stderr = Vec::new();
    let mut exit_code = None;
    let mut stderr_done = false;
    while exit_code.is_none() || !stderr_done {
        let mut out_chunk = Vec::new();
        let mut err_chunk = Vec::new();
        tokio::select! {
            _=cancel.cancelled()=>return Err("terminal cancelled; process tree terminated".into()),
            count=shell.stdout.read_until(0x1f,&mut out_chunk),if exit_code.is_none()=>{
                let count=count.map_err(|e|e.to_string())?;
                if count==0{return Err("persistent Bash terminated before completion".into());}
                if let Some(at)=find_subslice(&out_chunk,out_marker.as_bytes()){
                    stdout.extend_from_slice(&out_chunk[..at]);
                    let status=&out_chunk[at+out_marker.len()..];
                    if status.last()!=Some(&0x1f){return Err("malformed Bash end marker".into());}
                    let number=std::str::from_utf8(&status[..status.len()-1])
                        .map_err(|e|e.to_string())?;
                    exit_code=Some(number.parse::<i32>().map_err(|e|e.to_string())?);
                }else{stdout.extend_from_slice(&out_chunk);}
            }
            count=shell.stderr.read_until(0x1f,&mut err_chunk),if !stderr_done=>{
                let count=count.map_err(|e|e.to_string())?;
                if count==0{return Err("persistent Bash stderr terminated".into());}
                if let Some(at)=find_subslice(&err_chunk,err_marker.as_bytes()){
                    stderr.extend_from_slice(&err_chunk[..at]);
                    stderr_done=true;
                }else{stderr.extend_from_slice(&err_chunk);}
            }
        }
        if stdout.len() + stderr.len() > MAX_OUTPUT_BYTES {
            return Err(
                "terminal output collection limit exceeded; process tree terminated".into(),
            );
        }
    }
    let stdout = String::from_utf8(stdout).map_err(|_| "stdout is not UTF-8")?;
    let stderr = String::from_utf8(stderr).map_err(|_| "stderr is not UTF-8")?;
    let code = exit_code.ok_or("missing shell return code")?;
    let result = json!({"command":"","shell":"bash","shell_family":"bash","stdout":stdout,
        "stderr":stderr,"stdout_encoding":"utf-8","stderr_encoding":"utf-8",
        "returncode":code,"timed_out":false});
    Ok(result)
}
fn find_subslice(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|w| w == needle)
}

pub fn register_terminal_sessions(
    registry: &mut Registry,
    sessions: Arc<TerminalSessions>,
    cancellation: CancellationToken,
) -> Result<(), String> {
    registry.register(Tool{
        schema:ToolSchema{
            name:"terminal_run".into(),
            description:"Run persistent Bash commands on Owner host (default session reused across calls). session_id identifies one of max 2 host shells. exit closes a session. cmd/powershell remain one-shot on Windows.".into(),
            parameters:json!({"type":"object","properties":{
                "command":{"type":"string"},
                "shell":{"type":"string","enum":["bash","cmd","powershell"]},
                "session_id":{"type":"string"},
                "cwd":{"type":"string"},
                "temporary_outputs":{"type":"array","items":{"type":"string"}}
            },"required":["command"],"additionalProperties":false})
        },
        timeout:COMMAND_TIMEOUT+Duration::from_secs(10),
        invoke:Arc::new(move |args|{
            let sessions=sessions.clone();
            let token=cancellation.clone();
            Box::pin(async move {
                if args.get("shell").and_then(Value::as_str).unwrap_or("bash")=="bash"{
                    sessions.run(args,token).await
                }else{
                    if args.get("session_id").is_some(){
                        return Err("persistent session_id requires shell=bash".into());
                    }
                    let scratch=sessions.run.scratch_path()?;
                    super::terminal::execute(args,sessions.root.clone(),scratch,token).await
                }
            })
        }),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace_tools::Workspace;
    #[tokio::test]
    async fn persists_cwd_and_environment_between_commands() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().to_path_buf();
        let run = Arc::new(RunFiles::new(Workspace::new(&root).unwrap()).unwrap());
        let sessions = TerminalSessions::new(run.clone(), root.clone());
        sessions.run(json!({"command":"mkdir -p nested; cd nested; export MAI_TEST_SESSION_VAR=present"}),
            CancellationToken::new()).await.unwrap();
        let result = sessions
            .run(
                json!({"command":"pwd; printf '%s' \"$MAI_TEST_SESSION_VAR\""}),
                CancellationToken::new(),
            )
            .await
            .unwrap();
        let out = result["stdout"].as_str().unwrap();
        assert!(out.contains("nested"), "{out}");
        assert!(out.contains("present"), "{out}");
        sessions.close_all().await;
        run.cleanup().unwrap();
    }
    #[tokio::test]
    async fn nonzero_command_keeps_bash_session_state() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().to_path_buf();
        let run = Arc::new(RunFiles::new(Workspace::new(&root).unwrap()).unwrap());
        let sessions = TerminalSessions::new(run.clone(), root);
        sessions
            .run(
                json!({"command":"export MAI_PERSIST_AFTER_FAILURE=ok"}),
                CancellationToken::new(),
            )
            .await
            .unwrap();
        let failure = sessions
            .run(json!({"command":"false"}), CancellationToken::new())
            .await
            .unwrap_err();
        assert!(failure.contains("returncode"), "{failure}");
        let output = sessions
            .run(
                json!({"command":"printf '%s' \"$MAI_PERSIST_AFTER_FAILURE\""}),
                CancellationToken::new(),
            )
            .await
            .unwrap();
        assert_eq!(output["stdout"], "ok");
        sessions.close_all().await;
        run.cleanup().unwrap();
    }
    #[tokio::test]
    async fn rejects_third_session_and_exit_releases_slot() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().to_path_buf();
        let run = Arc::new(RunFiles::new(Workspace::new(&root).unwrap()).unwrap());
        let sessions = TerminalSessions::new(run.clone(), root);
        for id in ["one", "two"] {
            sessions
                .run(
                    json!({"session_id":id,"command":"true"}),
                    CancellationToken::new(),
                )
                .await
                .unwrap();
        }
        assert!(sessions
            .run(
                json!({"session_id":"three","command":"true"}),
                CancellationToken::new()
            )
            .await
            .unwrap_err()
            .contains("limit"));
        sessions
            .run(
                json!({"session_id":"one","command":"exit"}),
                CancellationToken::new(),
            )
            .await
            .unwrap();
        sessions
            .run(
                json!({"session_id":"three","command":"true"}),
                CancellationToken::new(),
            )
            .await
            .unwrap();
        sessions.close_all().await;
        run.cleanup().unwrap();
    }
    #[tokio::test]
    async fn cancellation_kills_persistent_shell() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().to_path_buf();
        let run = Arc::new(RunFiles::new(Workspace::new(&root).unwrap()).unwrap());
        let sessions = Arc::new(TerminalSessions::new(run.clone(), root));
        let token = CancellationToken::new();
        let cloned = token.clone();
        let other = sessions.clone();
        let task =
            tokio::spawn(async move { other.run(json!({"command":"sleep 6"}), cloned).await });
        tokio::time::sleep(Duration::from_millis(80)).await;
        token.cancel();
        let error = tokio::time::timeout(Duration::from_secs(3), task)
            .await
            .unwrap()
            .unwrap()
            .unwrap_err();
        assert!(error.contains("cancelled"));
        sessions.close_all().await;
        run.cleanup().unwrap();
    }
}
