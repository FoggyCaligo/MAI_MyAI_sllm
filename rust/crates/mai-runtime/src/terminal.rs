//! Opt-in terminal tool for the local preview. Its process-tree kill guard
//! runs on future cancellation/drop as well as on timeout/output exhaustion.
//! This does not sandbox arbitrary shell commands: restrict deployment to a
//! trusted owner, loopback and an explicitly allowed workspace.
use crate::file_mutations::RunFiles;
use crate::{Registry, Tool, ToolSchema};
use serde_json::{json, Value};
use std::{
    fs,
    path::{Component, Path, PathBuf},
    process::Stdio,
    sync::Arc,
    time::Duration,
};
use tokio::{
    io::AsyncReadExt,
    process::{Child, Command},
    time::sleep,
};
use tokio_util::sync::CancellationToken;

const MAX_COMMAND_CHARS: usize = 32_768;
const MAX_OUTPUT_BYTES: usize = 1_048_576;
const COMMAND_TIMEOUT: Duration = Duration::from_secs(180);

struct TreeGuard {
    child: Option<Child>,
    pid: u32,
}
impl TreeGuard {
    fn new(child: Child) -> Result<Self, String> {
        let pid = child.id().ok_or("spawned process has no PID")?;
        Ok(Self {
            child: Some(child),
            pid,
        })
    }
    fn child_mut(&mut self) -> Result<&mut Child, String> {
        self.child
            .as_mut()
            .ok_or_else(|| "child already reaped".into())
    }
    fn disarm(&mut self) {
        self.child = None;
    }
}
impl Drop for TreeGuard {
    fn drop(&mut self) {
        if self.child.is_none() {
            return;
        }
        // Sync Drop is intentional: future timeout/cancellation drops this
        // guard, and grandchildren must be signalled before scratch cleanup.
        terminate_tree(self.pid);
        if let Some(child) = self.child.as_mut() {
            let _ = child.start_kill();
        }
    }
}
#[cfg(unix)]
fn terminate_tree(pid: u32) {
    if pid <= i32::MAX as u32 {
        // All shell descendants inherit the newly created process group
        // unless they explicitly daemonize or create a new process group.
        unsafe {
            libc::kill(-(pid as i32), libc::SIGKILL);
        }
    }
}
#[cfg(windows)]
fn terminate_tree(pid: u32) {
    // taskkill /T /F traverses Windows child processes. Unlike job objects,
    // this is a best-effort cleanup and requires taskkill.exe availability.
    let _ = std::process::Command::new("taskkill.exe")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

#[cfg(windows)]
fn git_bash_path() -> Result<PathBuf, String> {
    // Unqualified "bash" on Windows can resolve to the WSL launcher in
    // System32. Only launch a confirmed Git for Windows bash.exe.
    if let Some(override_path) = std::env::var_os("MAI_GIT_BASH_PATH") {
        let path = PathBuf::from(override_path);
        if path.is_file() {
            return Ok(path);
        }
        return Err("MAI_GIT_BASH_PATH does not point to a file".into());
    }
    let mut candidates = Vec::new();
    for variable in ["ProgramFiles", "ProgramFiles(x86)"] {
        if let Some(folder) = std::env::var_os(variable) {
            candidates.push(
                PathBuf::from(folder)
                    .join("Git")
                    .join("bin")
                    .join("bash.exe"),
            );
        }
    }
    if let Some(folder) = std::env::var_os("LOCALAPPDATA") {
        candidates.push(
            PathBuf::from(folder)
                .join("Programs")
                .join("Git")
                .join("bin")
                .join("bash.exe"),
        );
    }
    for path in candidates {
        if path.is_file() {
            return Ok(path);
        }
    }
    Err("Git for Windows bash.exe not found; set MAI_GIT_BASH_PATH".into())
}

fn shell_args(
    shell: &str,
    command: &str,
    scratch: &Path,
) -> Result<(Command, Option<PathBuf>), String> {
    if command.trim().is_empty() || command.chars().count() > MAX_COMMAND_CHARS {
        return Err("terminal command must be 1..32768 characters".into());
    }
    let mut child = match shell {
        "bash" => {
            #[cfg(windows)]
            let program = git_bash_path()?;
            #[cfg(not(windows))]
            let program = PathBuf::from("bash");
            let mut c = Command::new(program);
            c.args(["--noprofile", "--norc", "-c", command]);
            c
        }
        "powershell" if cfg!(windows) => {
            let path = unique_script(scratch, "ps1", command)?;
            let mut c = Command::new("powershell.exe");
            c.args([
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
            ]);
            c.arg(&path);
            return Ok((c, Some(path)));
        }
        "cmd" if cfg!(windows) => {
            let path = unique_script(scratch, "cmd", command)?;
            let mut c = Command::new("cmd.exe");
            c.args(["/D", "/S", "/C"]);
            c.arg(&path);
            return Ok((c, Some(path)));
        }
        _ => return Err(format!("shell={shell} is not supported on this OS")),
    };
    child.kill_on_drop(true);
    Ok((child, None))
}
fn unique_script(scratch: &Path, extension: &str, source: &str) -> Result<PathBuf, String> {
    let dir = Path::new(scratch);
    let candidate = tempfile::Builder::new()
        .prefix("terminal-")
        .suffix(&format!(".{extension}"))
        .tempfile_in(dir)
        .map_err(|e| e.to_string())?;
    let (_, path) = candidate.keep().map_err(|e| e.to_string())?;
    // PowerShell -File reads UTF-8 with BOM reliably on legacy Windows PowerShell.
    let text = if extension == "ps1" {
        format!("\u{feff}{source}")
    } else {
        source.to_string()
    };
    fs::write(&path, text).map_err(|e| e.to_string())?;
    Ok(path)
}
fn checked_cwd(root: &Path, scratch: &Path, requested: Option<&str>) -> Result<PathBuf, String> {
    let target = match requested {
        None => root.to_path_buf(),
        Some(v) if v.trim().is_empty() => return Err("empty cwd".into()),
        Some(v) => {
            let path = Path::new(v);
            if path.is_absolute() {
                path.to_path_buf()
            } else {
                root.join(path)
            }
        }
    };
    // Windows canonicalization may add the extended-length \\?\ prefix;
    // compare only canonical paths, never canonical cwd to lexical root.
    let real = fs::canonicalize(target).map_err(|e| format!("terminal cwd: {e}"))?;
    let canonical_root = fs::canonicalize(root).map_err(|e| format!("terminal root: {e}"))?;
    let canonical_scratch =
        fs::canonicalize(scratch).map_err(|e| format!("terminal scratch: {e}"))?;
    if !real.is_dir() || !real.starts_with(&canonical_root) {
        return Err("terminal cwd must be an existing directory inside workspace".into());
    }
    let tmp = canonical_root.join("tmp");
    if real.starts_with(tmp) && !real.starts_with(&canonical_scratch) {
        return Err("terminal cwd cannot be another run's scratch".into());
    }
    Ok(real)
}
fn temporary_outputs(args: &Value, cwd: &Path, scratch: &Path) -> Result<Vec<PathBuf>, String> {
    let Some(items) = args.get("temporary_outputs") else {
        return Ok(vec![]);
    };
    let items = items
        .as_array()
        .ok_or("temporary_outputs must be an array")?;
    if items.is_empty() {
        return Ok(vec![]);
    }
    if cwd != scratch {
        return Err("temporary_outputs requires cwd to be this run's scratch directory".into());
    }
    let mut paths = Vec::with_capacity(items.len());
    for item in items {
        let raw = item
            .as_str()
            .ok_or("temporary output names must be strings")?;
        let rel = Path::new(raw);
        if raw.is_empty()
            || raw.contains('\\')
            || rel.is_absolute()
            || rel.components().any(|c| !matches!(c, Component::Normal(_)))
        {
            return Err("temporary output must be a safe relative path".into());
        }
        let target = scratch.join(rel);
        if target.exists() || target.is_symlink() {
            return Err("temporary output already exists".into());
        }
        if paths.contains(&target) {
            return Err("temporary output names must be unique".into());
        }
        // Forbid symlink ancestors already present under scratch.
        let mut parent = target.parent();
        while let Some(dir) = parent {
            if dir == scratch {
                break;
            }
            if dir.is_symlink() {
                return Err("temporary output parent must not be symlink".into());
            }
            parent = dir.parent();
        }
        paths.push(target);
    }
    Ok(paths)
}
fn nested_powershell(command: &str) -> bool {
    let c = command
        .trim_start()
        .trim_start_matches('&')
        .trim_start()
        .to_ascii_lowercase();
    let executable = ["powershell", "powershell.exe", "pwsh", "pwsh.exe"];
    for start in executable {
        if let Some(rest) = c.strip_prefix(start) {
            if rest.starts_with(char::is_whitespace) {
                let words = rest.split_whitespace().collect::<Vec<_>>();
                if words
                    .iter()
                    .any(|w| matches!(*w, "-command" | "-encodedcommand" | "-c" | "-enc"))
                {
                    return true;
                }
            }
        }
    }
    false
}

/// Executes one OS command inside the trusted, opt-in local workspace.
/// A returned error includes a bounded status snapshot for model recovery.
pub async fn execute(
    args: Value,
    root: PathBuf,
    scratch: PathBuf,
    cancellation: CancellationToken,
) -> Result<Value, String> {
    let command = args
        .get("command")
        .and_then(Value::as_str)
        .ok_or("missing command")?;
    let shell = args.get("shell").and_then(Value::as_str).unwrap_or("bash");
    if shell == "powershell" && nested_powershell(command) {
        return Err("shell=powershell already invokes PowerShell; do not nest -Command".into());
    }
    let cwd = checked_cwd(&root, &scratch, args.get("cwd").and_then(Value::as_str))?;
    let _declared = temporary_outputs(&args, &cwd, &scratch)?;
    let (mut cmd, script) = shell_args(shell, command, &scratch)?;
    cmd.current_dir(&cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.as_std_mut().process_group(0);
    }
    let result = run_child(cmd, command, &cwd, shell, &cancellation).await;
    if let Some(script) = script {
        let _ = fs::remove_file(script);
    }
    result
}
async fn run_child(
    mut cmd: Command,
    command: &str,
    cwd: &Path,
    shell: &str,
    cancellation: &CancellationToken,
) -> Result<Value, String> {
    if cancellation.is_cancelled() {
        return Err("terminal cancelled before spawn".into());
    }
    let child = cmd.spawn().map_err(|e| format!("terminal spawn: {e}"))?;
    let mut guard = TreeGuard::new(child)?;
    let mut stdout = guard
        .child_mut()?
        .stdout
        .take()
        .ok_or("missing stdout pipe")?;
    let mut stderr = guard
        .child_mut()?
        .stderr
        .take()
        .ok_or("missing stderr pipe")?;
    let mut stdout_bytes = Vec::new();
    let mut stderr_bytes = Vec::new();
    let mut out_done = false;
    let mut err_done = false;
    let mut status = None;
    let deadline = sleep(COMMAND_TIMEOUT);
    tokio::pin!(deadline);
    let mut out_buffer = [0u8; 8192];
    let mut err_buffer = [0u8; 8192];
    while status.is_none() || !out_done || !err_done {
        tokio::select! {
            _=cancellation.cancelled()=>return Err("terminal cancelled; process tree terminated".into()),
            _=&mut deadline=>return Err(format!("terminal timed out after {} seconds; process tree terminated",
                COMMAND_TIMEOUT.as_secs())),
            read=stdout.read(&mut out_buffer),if !out_done=>{
                let count=read.map_err(|e|format!("terminal stdout: {e}"))?;
                if count==0 {out_done=true;}else{
                    if stdout_bytes.len()+stderr_bytes.len()+count>MAX_OUTPUT_BYTES {
                        return Err(format!("terminal output collection limit exceeded: {}",MAX_OUTPUT_BYTES));
                    }
                    stdout_bytes.extend_from_slice(&out_buffer[..count]);
                }
            }
            read=stderr.read(&mut err_buffer),if !err_done=>{
                let count=read.map_err(|e|format!("terminal stderr: {e}"))?;
                if count==0 {err_done=true;}else{
                    if stdout_bytes.len()+stderr_bytes.len()+count>MAX_OUTPUT_BYTES {
                        return Err(format!("terminal output collection limit exceeded: {}",MAX_OUTPUT_BYTES));
                    }
                    stderr_bytes.extend_from_slice(&err_buffer[..count]);
                }
            }
            exit=guard.child_mut()?.wait(), if status.is_none()=>{
                status=Some(exit.map_err(|e|format!("terminal wait: {e}"))?);
            }
        }
    }
    guard.disarm();
    let stdout = String::from_utf8(stdout_bytes).map_err(|_| "stdout is not UTF-8".to_string())?;
    let stderr = String::from_utf8(stderr_bytes).map_err(|_| "stderr is not UTF-8".to_string())?;
    let exit = status.ok_or("missing exit status")?;
    let payload = json!({
        "command":command,"cwd":cwd.to_string_lossy(),
        "shell":shell,"shell_family":shell,
        "stdout":stdout,"stderr":stderr,
        "stdout_encoding":"utf-8","stderr_encoding":"utf-8",
        "returncode":exit.code(),"timed_out":false
    });
    if !exit.success() {
        return Err(format!("terminal command failed: {payload}"));
    }
    Ok(payload)
}

/// Terminal is *not* a sandbox. Only call this behind a deliberate owner opt-in.
pub fn register_terminal(
    registry: &mut Registry,
    run: Arc<RunFiles>,
    cancellation: CancellationToken,
    root: PathBuf,
) -> Result<(), String> {
    registry.register(Tool{
        schema:ToolSchema{
            name:"terminal_run".into(),
            description:"Execute a local Bash shell script (Git Bash on Windows), or cmd/powershell on Windows. Only for trusted local preview. Use temporary_outputs solely in the run scratch cwd.".into(),
            parameters:json!({"type":"object","properties":{
                "command":{"type":"string"},"cwd":{"type":"string"},
                "shell":{"type":"string","enum":["bash","cmd","powershell"]},
                "temporary_outputs":{"type":"array","items":{"type":"string"}}
            },"required":["command"],"additionalProperties":false}),
        },
        timeout:COMMAND_TIMEOUT + Duration::from_secs(10),
        invoke:Arc::new(move |args| {
            let run=run.clone();
            let cancel=cancellation.clone();
            let root=root.clone();
            Box::pin(async move {
                let scratch=run.scratch_path()?;
                execute(args,root,scratch,cancel).await
            })
        }),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejected_shells_and_output_paths() {
        assert!(nested_powershell("powershell -NoProfile -Command ls"));
        assert!(!nested_powershell("Get-ChildItem; powershell -Command ls"));
        let root = tempfile::tempdir().unwrap();
        let scratch = root.path();
        assert!(temporary_outputs(
            &json!({"temporary_outputs":["../outside"]}),
            scratch,
            scratch
        )
        .is_err());
        assert!(temporary_outputs(
            &json!({"temporary_outputs":["file","file"]}),
            scratch,
            scratch
        )
        .is_err());
        assert!(
            temporary_outputs(&json!({"temporary_outputs":["existing"]}), scratch, scratch).is_ok()
        );
        assert!(shell_args("madeup", "echo hi", scratch).is_err());
        #[cfg(unix)]
        assert!(shell_args("powershell", "Write-Host yes", scratch).is_err());
    }
    #[tokio::test]
    async fn stdout_stderr_and_nonzero_exit() {
        let root = tempfile::tempdir().unwrap();
        let args = json!({"command":"printf 'out'; printf 'err' >&2","shell":"bash"});
        let out = execute(
            args,
            root.path().to_path_buf(),
            root.path().to_path_buf(),
            CancellationToken::new(),
        )
        .await
        .unwrap();
        assert_eq!(out["stdout"], "out");
        assert_eq!(out["stderr"], "err");
        let fail = execute(
            json!({"command":"exit 7"}),
            root.path().to_path_buf(),
            root.path().to_path_buf(),
            CancellationToken::new(),
        )
        .await
        .unwrap_err();
        assert!(fail.contains("returncode"));
    }
    #[tokio::test]
    async fn cancellation_interrupts_running_command() {
        let root = tempfile::tempdir().unwrap();
        let cancel = CancellationToken::new();
        let cloned = cancel.clone();
        let dir = root.path().to_path_buf();
        let task = tokio::spawn(async move {
            execute(json!({"command":"sleep 4"}), dir.clone(), dir, cloned).await
        });
        sleep(Duration::from_millis(50)).await;
        cancel.cancel();
        let result = tokio::time::timeout(Duration::from_secs(3), task)
            .await
            .expect("terminal must abort promptly")
            .unwrap();
        assert!(result.unwrap_err().contains("cancelled"));
    }
    #[tokio::test]
    async fn stdout_limit_kills_child() {
        let root = tempfile::tempdir().unwrap();
        let out = execute(
            json!({"command":"head -c 1100000 /dev/zero"}),
            root.path().to_path_buf(),
            root.path().to_path_buf(),
            CancellationToken::new(),
        )
        .await
        .unwrap_err();
        assert!(out.contains("output collection limit exceeded"));
    }
    #[cfg(unix)]
    #[tokio::test]
    async fn cancellation_terminates_background_shell_descendant() {
        let root = tempfile::tempdir().unwrap();
        let token = CancellationToken::new();
        let shared = token.clone();
        let dir = root.path().to_path_buf();
        let task = tokio::spawn(async move {
            execute(
                json!({"command":"(sleep 1; printf orphan > descendant.txt) & wait"}),
                dir.clone(),
                dir,
                shared,
            )
            .await
        });
        sleep(Duration::from_millis(100)).await;
        token.cancel();
        assert!(tokio::time::timeout(Duration::from_secs(3), task)
            .await
            .unwrap()
            .unwrap()
            .is_err());
        sleep(Duration::from_millis(1150)).await;
        assert!(
            !root.path().join("descendant.txt").exists(),
            "cancelled background child must not survive to write output"
        );
    }
    #[tokio::test]
    async fn cancelled_before_spawn_never_executes_command() {
        let root = tempfile::tempdir().unwrap();
        let cancel = CancellationToken::new();
        cancel.cancel();
        let result = execute(
            json!({"command":"printf leaked > no.txt"}),
            root.path().to_path_buf(),
            root.path().to_path_buf(),
            cancel,
        )
        .await;
        assert!(result.unwrap_err().contains("cancelled"));
        assert!(!root.path().join("no.txt").exists());
    }
    #[cfg(windows)]
    #[test]
    fn git_bash_resolves_to_real_git_installation() {
        let shell = git_bash_path().expect("Git for Windows required in Windows CI");
        assert!(shell.is_file());
        assert_eq!(shell.file_name().and_then(|v| v.to_str()), Some("bash.exe"));
        assert!(shell.to_string_lossy().to_ascii_lowercase().contains("git"));
    }
    #[cfg(windows)]
    #[tokio::test]
    async fn windows_shells_execute_without_nested_wrappers() {
        let root=tempfile::tempdir().unwrap();
        let base=root.path().to_path_buf();
        let cmd=execute(json!({"shell":"cmd","command":"@echo off\r\necho cmd-ok"}),
            base.clone(),base.clone(),CancellationToken::new()).await.unwrap();
        assert!(cmd["stdout"].as_str().unwrap().contains("cmd-ok"));
        let ps=execute(json!({"shell":"powershell","command":"Write-Output 'ps-ok'"}),
            base.clone(),base, CancellationToken::new()).await.unwrap();
        assert!(ps["stdout"].as_str().unwrap().contains("ps-ok"));
    }

}
