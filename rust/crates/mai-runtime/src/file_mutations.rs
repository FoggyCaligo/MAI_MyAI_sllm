//! Run-owned native filesystem mutations. No MCP transport.
//! The job may be cancelled while a spawn_blocking operation is queued:
//! each operation and final cleanup share one mutex, and cleanup seals the run.
use crate::workspace_tools::Workspace;
use crate::{Registry, Tool, ToolSchema};
use serde_json::{json, Value};
use std::{
    fs,
    io::Write,
    path::{Component, Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};

struct RunState {
    scratch: Option<tempfile::TempDir>,
    closed: bool,
}

/// An independent temporary directory inside <workspace>/tmp, owned by one
/// agent run. No operation may access the scratch directories of other runs.
pub struct RunFiles {
    workspace: Workspace,
    state: Mutex<RunState>,
}
impl RunFiles {
    pub fn new(workspace: Workspace) -> Result<Self, String> {
        let base = workspace.root().join("tmp");
        match fs::symlink_metadata(&base) {
            Ok(meta) if meta.file_type().is_symlink() || !meta.is_dir() => {
                return Err("workspace tmp must be an ordinary directory".into())
            }
            Ok(_) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                fs::create_dir(&base).map_err(|e| format!("create tmp: {e}"))?
            }
            Err(e) => return Err(format!("inspect tmp: {e}")),
        }
        let canonical = fs::canonicalize(&base).map_err(|e| e.to_string())?;
        if !canonical.starts_with(workspace.root()) {
            return Err("workspace tmp escapes root".into());
        }
        let scratch = tempfile::Builder::new()
            .prefix("run-")
            .tempdir_in(&canonical)
            .map_err(|e| format!("run scratch: {e}"))?;
        Ok(Self {
            workspace,
            state: Mutex::new(RunState {
                scratch: Some(scratch),
                closed: false,
            }),
        })
    }
    pub fn scratch_path(&self) -> Result<PathBuf, String> {
        let state = self.state.lock().map_err(|_| "run files lock poisoned")?;
        if state.closed {
            return Err("run already finished".into());
        }
        state
            .scratch
            .as_ref()
            .map(|d| d.path().to_path_buf())
            .ok_or_else(|| "scratch directory no longer exists".into())
    }
    fn resolve(&self, path: &str, scratch: &Path) -> Result<PathBuf, String> {
        let target = self.workspace.path(path)?;
        if target.starts_with(self.workspace.root().join("tmp")) && !target.starts_with(scratch) {
            return Err("cannot access another run's temporary directory".into());
        }
        Ok(target)
    }
    fn temporary_path(&self, relative: &str, scratch: &Path) -> Result<PathBuf, String> {
        let raw = Path::new(relative);
        if relative.is_empty()
            || raw.is_absolute()
            || relative.contains('\\')
            || raw.components().any(|c| !matches!(c, Component::Normal(_)))
        {
            return Err("temporary path must be a safe relative path".into());
        }
        let target = self.workspace.path(&scratch.join(raw).to_string_lossy())?;
        if !target.starts_with(scratch) || target == scratch {
            return Err("temporary path leaves current run".into());
        }
        Ok(target)
    }
    fn mutate(
        &self,
        args: &Value,
        action: fn(&Self, &Value, &Path) -> Result<Value, String>,
    ) -> Result<Value, String> {
        let state = self.state.lock().map_err(|_| "run files lock poisoned")?;
        if state.closed {
            return Err("run already finished".into());
        }
        let scratch = state.scratch.as_ref().ok_or("scratch missing")?.path();
        action(self, args, scratch)
    }
    /// Seal this run and delete only its own scratch directory.
    /// Operations queued after this call refuse to execute.
    pub fn cleanup(&self) -> Result<(), String> {
        let mut state = self.state.lock().map_err(|_| "run files lock poisoned")?;
        if state.closed {
            return Ok(());
        }
        state.closed = true;
        if let Some(dir) = state.scratch.take() {
            dir.close()
                .map_err(|e| format!("run scratch cleanup: {e}"))?;
        }
        Ok(())
    }
}
fn text<'a>(v: &'a Value, key: &str) -> Result<&'a str, String> {
    v.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("missing string: {key}"))
}
fn flag(v: &Value, key: &str) -> bool {
    v.get(key).and_then(Value::as_bool).unwrap_or(false)
}
fn write_file(run: &RunFiles, args: &Value, scratch: &Path) -> Result<Value, String> {
    let name = text(args, "path")?;
    let content = text(args, "content")?;
    if content.len() > 2_000_000 {
        return Err("write exceeds 2 MB limit".into());
    }
    if args
        .get("encoding")
        .and_then(Value::as_str)
        .unwrap_or("utf-8")
        != "utf-8"
    {
        return Err("only UTF-8 writes are implemented".into());
    }
    let temporary = match args
        .get("lifecycle")
        .and_then(Value::as_str)
        .unwrap_or("persistent")
    {
        "temporary" => true,
        "persistent" => false,
        _ => return Err("invalid lifecycle".into()),
    };
    let path = if temporary {
        run.temporary_path(name, scratch)?
    } else {
        run.resolve(name, scratch)?
    };
    if path == run.workspace.root() || path == scratch {
        return Err("file_write destination is a protected directory".into());
    }
    if temporary || flag(args, "create_parents") {
        fs::create_dir_all(path.parent().ok_or("missing parent")?).map_err(|e| e.to_string())?;
    }
    // Check again following directory creation (and any symlink substitution).
    let path = run.resolve(&path.to_string_lossy(), scratch)?;
    let existed = path.exists();
    if temporary && existed {
        return Err("temporary file already exists".into());
    }
    if existed && !path.is_file() {
        return Err("destination is not a file".into());
    }
    let mut staged = tempfile::NamedTempFile::new_in(path.parent().ok_or("missing parent")?)
        .map_err(|e| e.to_string())?;
    staged
        .write_all(content.as_bytes())
        .map_err(|e| e.to_string())?;
    if existed {
        let permissions = fs::metadata(&path)
            .map_err(|e| e.to_string())?
            .permissions();
        staged
            .as_file()
            .set_permissions(permissions)
            .map_err(|e| e.to_string())?;
    }
    staged
        .persist(&path)
        .map_err(|e| format!("atomic write: {e}"))?;
    Ok(
        json!({"path":path.to_string_lossy(),"bytes":content.len(),"created":!existed,
        "lifecycle":if temporary{"temporary"}else if existed{"existing"}else{"persistent"}}),
    )
}
fn delete_file(run: &RunFiles, args: &Value, scratch: &Path) -> Result<Value, String> {
    let path = run.resolve(text(args, "path")?, scratch)?;
    let tmp_base = run.workspace.root().join("tmp");
    if path == run.workspace.root() || path == tmp_base || path == scratch {
        return Err("cannot delete workspace or temporary root".into());
    }
    if path.is_symlink() {
        return Err("refusing to delete symlink in preview".into());
    }
    if path.is_dir() {
        if flag(args, "recursive") {
            // Refuse symlink contents rather than relying on platform-specific behavior.
            for entry in walkdir::WalkDir::new(&path).follow_links(false) {
                let entry = entry.map_err(|e| e.to_string())?;
                if entry.file_type().is_symlink() {
                    return Err("recursive delete of symlink subtree refused".into());
                }
            }
            fs::remove_dir_all(&path).map_err(|e| e.to_string())?;
        } else {
            fs::remove_dir(&path).map_err(|e| e.to_string())?;
        }
    } else {
        fs::remove_file(&path).map_err(|e| e.to_string())?;
    }
    Ok(json!({"path":path.to_string_lossy(),"deleted":true}))
}
fn relocate(run: &RunFiles, args: &Value, scratch: &Path, copy: bool) -> Result<Value, String> {
    let source = run.resolve(text(args, "source")?, scratch)?;
    let destination = run.resolve(text(args, "destination")?, scratch)?;
    if source == run.workspace.root()
        || source == scratch
        || destination == run.workspace.root()
        || destination == scratch
    {
        return Err("protected directory cannot be moved/copied".into());
    }
    if !source.is_file() || source.is_symlink() {
        return Err("preview move/copy supports regular files only".into());
    }
    if destination.exists() {
        return Err("destination already exists".into());
    }
    if !copy && source.starts_with(scratch) && !destination.starts_with(scratch) {
        return Err("temporary files cannot be moved outside their run".into());
    }
    if flag(args, "create_parents") {
        fs::create_dir_all(destination.parent().ok_or("missing parent")?)
            .map_err(|e| e.to_string())?;
    }
    let destination = run.resolve(&destination.to_string_lossy(), scratch)?;
    if destination.exists() {
        return Err("destination already exists".into());
    }
    if copy {
        fs::copy(&source, &destination).map_err(|e| e.to_string())?;
    } else {
        fs::rename(&source, &destination).map_err(|e| e.to_string())?;
    }
    Ok(json!({"source":source.to_string_lossy(),"destination":destination.to_string_lossy()}))
}
fn move_file(run: &RunFiles, args: &Value, scratch: &Path) -> Result<Value, String> {
    relocate(run, args, scratch, false)
}
fn copy_file(run: &RunFiles, args: &Value, scratch: &Path) -> Result<Value, String> {
    relocate(run, args, scratch, true)
}
fn edit_code(run: &RunFiles, args: &Value, scratch: &Path) -> Result<Value, String> {
    let path = run.resolve(text(args, "path")?, scratch)?;
    let old = text(args, "old_text")?;
    let new = text(args, "new_text")?;
    if old.is_empty() {
        return Err("old_text must not be empty".into());
    }
    let source = fs::read_to_string(&path).map_err(|e| e.to_string())?;
    if source.len() > 4_000_000 {
        return Err("file exceeds editing limit".into());
    }
    let count = source.matches(old).count();
    if count != 1 {
        return Err(format!(
            "code_edit requires exactly one match, found {count}"
        ));
    }
    if old == new {
        return Err("replacement has no effect".into());
    }
    let result = source.replacen(old, new, 1);
    let mut staged = tempfile::NamedTempFile::new_in(path.parent().ok_or("missing parent")?)
        .map_err(|e| e.to_string())?;
    staged
        .write_all(result.as_bytes())
        .map_err(|e| e.to_string())?;
    staged
        .as_file()
        .set_permissions(
            fs::metadata(&path)
                .map_err(|e| e.to_string())?
                .permissions(),
        )
        .map_err(|e| e.to_string())?;
    staged.persist(&path).map_err(|e| e.to_string())?;
    Ok(json!({"path":path.to_string_lossy(),"replacements":1,"bytes":result.len()}))
}
fn add(
    registry: &mut Registry,
    run: &Arc<RunFiles>,
    name: &str,
    description: &str,
    parameters: Value,
    handler: fn(&RunFiles, &Value, &Path) -> Result<Value, String>,
) -> Result<(), String> {
    let run = Arc::clone(run);
    registry.register(Tool {
        schema: ToolSchema {
            name: name.into(),
            description: description.into(),
            parameters,
        },
        timeout: Duration::from_secs(60),
        invoke: Arc::new(move |args| {
            let run = Arc::clone(&run);
            Box::pin(async move {
                tokio::task::spawn_blocking(move || run.mutate(&args, handler))
                    .await
                    .map_err(|e| format!("file tool worker: {e}"))?
            })
        }),
    })
}
/// Register write/move/copy/delete/edit capabilities for exactly one active run.
/// Do not register unscoped code_edit alongside this scoped version.
pub fn register_run_file_tools(registry: &mut Registry, run: &Arc<RunFiles>) -> Result<(), String> {
    add(
        registry,
        run,
        "file_write",
        "Write UTF-8 workspace text; temporary paths live only under this run",
        json!({"type":"object","properties":{"path":{"type":"string"},
        "content":{"type":"string"},"encoding":{"type":"string"},
        "create_parents":{"type":"boolean"},"lifecycle":{"type":"string",
        "enum":["temporary","persistent"]}},
        "required":["path","content"],"additionalProperties":false}),
        write_file,
    )?;
    add(
        registry,
        run,
        "file_delete",
        "Delete a workspace path, excluding scratch/workspace roots",
        json!({"type":"object","properties":{"path":{"type":"string"},
        "recursive":{"type":"boolean"}},
        "required":["path"],"additionalProperties":false}),
        delete_file,
    )?;
    let relocate_schema = json!({"type":"object","properties":{
        "source":{"type":"string"},"destination":{"type":"string"},
        "create_parents":{"type":"boolean"}},
        "required":["source","destination"],"additionalProperties":false});
    add(
        registry,
        run,
        "file_move",
        "Move workspace regular file without overwriting destination",
        relocate_schema.clone(),
        move_file,
    )?;
    add(
        registry,
        run,
        "file_copy",
        "Copy workspace regular file without overwriting destination",
        relocate_schema,
        copy_file,
    )?;
    add(
        registry,
        run,
        "code_edit",
        "Replace exactly one occurrence in existing workspace source",
        json!({"type":"object","properties":{"path":{"type":"string"},
        "old_text":{"type":"string"},"new_text":{"type":"string"}},
        "required":["path","old_text","new_text"],"additionalProperties":false}),
        edit_code,
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (tempfile::TempDir, Arc<RunFiles>) {
        let root = tempfile::tempdir().unwrap();
        let ws = Workspace::new(root.path()).unwrap();
        let run = Arc::new(RunFiles::new(ws).unwrap());
        (root, run)
    }
    #[test]
    fn temporary_artifacts_are_per_run_and_cleanup_is_idempotent() {
        let (root, a) = fixture();
        let b = Arc::new(RunFiles::new(Workspace::new(root.path()).unwrap()).unwrap());
        let own = write_file(
            &a,
            &json!({"path":"scripts/test.sh","content":"hi",
            "lifecycle":"temporary"}),
            &a.scratch_path().unwrap(),
        )
        .unwrap();
        let original = own["path"].as_str().unwrap().to_string();
        let other = write_file(
            &b,
            &json!({"path":"keep.txt","content":"safe",
            "lifecycle":"temporary"}),
            &b.scratch_path().unwrap(),
        )
        .unwrap();
        assert!(Path::new(&original).exists());
        a.cleanup().unwrap();
        a.cleanup().unwrap();
        assert!(!Path::new(&original).exists());
        assert!(Path::new(other["path"].as_str().unwrap()).exists());
        b.cleanup().unwrap();
    }
    #[test]
    fn permanent_and_existing_files_are_never_cleaned() {
        let (root, run) = fixture();
        let scratch = run.scratch_path().unwrap();
        fs::write(root.path().join("existing.txt"), "old").unwrap();
        let result = write_file(
            &run,
            &json!({"path":"existing.txt","content":"new"}),
            &scratch,
        )
        .unwrap();
        assert_eq!(result["lifecycle"], "existing");
        write_file(
            &run,
            &json!({"path":"persistent.txt","content":"keep"}),
            &scratch,
        )
        .unwrap();
        run.cleanup().unwrap();
        assert_eq!(
            fs::read_to_string(root.path().join("existing.txt")).unwrap(),
            "new"
        );
        assert_eq!(
            fs::read_to_string(root.path().join("persistent.txt")).unwrap(),
            "keep"
        );
    }
    #[test]
    fn existing_temporary_name_and_other_runs_are_not_accessible() {
        let (root, a) = fixture();
        let b = RunFiles::new(Workspace::new(root.path()).unwrap()).unwrap();
        let scratch = a.scratch_path().unwrap();
        write_file(
            &a,
            &json!({"path":"test","content":"a","lifecycle":"temporary"}),
            &scratch,
        )
        .unwrap();
        assert!(write_file(
            &a,
            &json!({"path":"test","content":"overwrite","lifecycle":"temporary"}),
            &scratch
        )
        .is_err());
        assert!(delete_file(
            &a,
            &json!({"path":b.scratch_path().unwrap().to_string_lossy()}),
            &scratch
        )
        .is_err());
        assert!(write_file(
            &a,
            &json!({"path":"../outside","content":"x","lifecycle":"temporary"}),
            &scratch
        )
        .is_err());
        assert!(!root.path().join("outside").exists());
    }
    #[test]
    fn move_copy_delete_contracts() {
        let (root, run) = fixture();
        let scratch = run.scratch_path().unwrap();
        fs::write(root.path().join("source.txt"), "source").unwrap();
        copy_file(
            &run,
            &json!({"source":"source.txt","destination":"copy.txt"}),
            &scratch,
        )
        .unwrap();
        assert!(copy_file(
            &run,
            &json!({"source":"source.txt","destination":"copy.txt"}),
            &scratch
        )
        .is_err());
        move_file(
            &run,
            &json!({"source":"copy.txt","destination":"moved.txt"}),
            &scratch,
        )
        .unwrap();
        delete_file(&run, &json!({"path":"moved.txt"}), &scratch).unwrap();
        assert!(!root.path().join("moved.txt").exists());
        assert!(root.path().join("source.txt").exists());
    }
    #[test]
    fn sealed_run_rejects_late_mutations() {
        let (_root, run) = fixture();
        run.cleanup().unwrap();
        assert!(run
            .mutate(&json!({"path":"after","content":"no"}), write_file)
            .is_err());
    }
    fn slow_test_write(run:&RunFiles,args:&Value,scratch:&Path)->Result<Value,String>{
        fs::write(scratch.join("entered"),"ready").map_err(|e|e.to_string())?;
        std::thread::sleep(Duration::from_millis(40));
        write_file(run,args,scratch)
    }
    #[test]
    fn cleanup_waits_for_inflight_write_then_seals_run(){
        let (root,run)=fixture();
        let scratch=run.scratch_path().unwrap();
        let request=json!({"path":"race.txt","content":"temporary",
            "lifecycle":"temporary"});
        let worker_run=Arc::clone(&run);
        let handle=std::thread::spawn(move||worker_run.mutate(&request,slow_test_write));
        let started=scratch.join("entered");
        for _ in 0..500 {
            if started.exists(){break;}
            std::thread::sleep(Duration::from_millis(1));
        }
        assert!(started.exists(),"worker never acquired the run lock");
        run.cleanup().unwrap();
        assert!(handle.join().unwrap().is_ok());
        assert!(!scratch.exists());
        assert!(root.path().join("tmp").exists());
        assert!(run.mutate(&json!({"path":"no","content":"later"}),write_file).is_err());
    }
    #[test]
    fn registry_definitions_have_no_unscoped_mutation_path(){
        let (_root,run)=fixture();
        let mut registry=Registry::new();
        register_run_file_tools(&mut registry,&run).unwrap();
        assert_eq!(registry.names(),
            vec!["code_edit","file_copy","file_delete","file_move","file_write"]);
        run.cleanup().unwrap();
    }

}
