//! Workspace-scoped native file and code tools. Each definition is registered
//! once; future Host/Client dispatch may reuse these schemas unchanged.
//! Unlike legacy owner tools, preview access is restricted to a canonical root.
//! No symlinks are followed for directory traversal, and no path escapes root.
use crate::{Registry, Tool, ToolSchema};
use serde_json::{json, Value};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use walkdir::WalkDir;

#[derive(Clone)]
pub struct Workspace {
    root: Arc<PathBuf>,
}
impl Workspace {
    pub(crate) fn root(&self) -> &Path { self.root.as_path() }
    pub fn new(root: impl AsRef<Path>) -> Result<Self, String> {
        let root = fs::canonicalize(root).map_err(|e| format!("workspace root: {e}"))?;
        if !root.is_dir() {
            return Err("workspace root is not directory".into());
        }
        Ok(Self {
            root: Arc::new(root),
        })
    }
    pub(crate) fn path(&self, input: &str) -> Result<PathBuf, String> {
        if input.trim().is_empty() {
            return Err("empty path".into());
        }
        let raw = PathBuf::from(input);
        let target = if raw.is_absolute() {
            raw
        } else {
            self.root.join(raw)
        };
        // Canonicalize every existing component, including symlinks. For new
        // paths, find the nearest existing ancestor before allowing creation.
        let mut ancestor = target.as_path();
        while !ancestor.exists() {
            ancestor = ancestor.parent().ok_or("invalid filesystem path")?;
        }
        let canonical = fs::canonicalize(ancestor).map_err(|e| e.to_string())?;
        if !canonical.starts_with(self.root.as_path()) {
            return Err("path outside allowed workspace".into());
        }
        // Verify relative lexical path does not contain .. escapes even when
        // components after ancestor do not exist yet.
        let suffix = target.strip_prefix(ancestor).map_err(|e| e.to_string())?;
        let mut resolved = canonical;
        for component in suffix.components() {
            use std::path::Component;
            match component {
                Component::Normal(name) => resolved.push(name),
                Component::CurDir => {}
                _ => return Err("unsafe path component".into()),
            }
        }
        if !resolved.starts_with(self.root.as_path()) {
            return Err("path outside allowed workspace".into());
        }
        Ok(resolved)
    }
}
fn str_arg<'a>(v: &'a Value, key: &str) -> Result<&'a str, String> {
    v.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("missing string argument: {key}"))
}
fn bool_arg(v: &Value, key: &str, default: bool) -> bool {
    v.get(key).and_then(Value::as_bool).unwrap_or(default)
}
fn integer_arg(
    v: &Value,
    key: &str,
    default: usize,
    min: usize,
    max: usize,
) -> Result<usize, String> {
    let n = match v.get(key) {
        Some(raw) => {
            raw.as_u64()
                .ok_or_else(|| format!("{key} must be positive integer"))? as usize
        }
        None => default,
    };
    if !(min..=max).contains(&n) {
        return Err(format!("{key} outside {min}..={max}"));
    }
    Ok(n)
}
fn text_from_file(path: &Path, limit: usize) -> Result<String, String> {
    let meta = fs::metadata(path).map_err(|e| e.to_string())?;
    if !meta.is_file() {
        return Err("expected regular file".into());
    }
    if meta.len() > limit as u64 {
        return Err("file exceeds tool read limit".into());
    }
    fs::read_to_string(path).map_err(|e| format!("UTF-8 read: {e}"))
}
fn result_window(count: usize, more: bool) -> Value {
    json!({"returned_count":count,"total_count":if more{None}else{Some(count)},
        "has_more":more,"complete":!more})
}
fn list(ws: &Workspace, arg: &Value) -> Result<Value, String> {
    let path = ws.path(arg.get("path").and_then(Value::as_str).unwrap_or("."))?;
    if !path.is_dir() {
        return Err("file_list path must be directory".into());
    }
    let recursive = bool_arg(arg, "recursive", false);
    let dirs_only = bool_arg(arg, "directories_only", false);
    let max = integer_arg(arg, "max_items", 500, 1, 10_000)?;
    let walker = WalkDir::new(&path)
        .min_depth(1)
        .max_depth(if recursive { usize::MAX } else { 1 })
        .follow_links(false)
        .sort_by_file_name();
    let mut items = Vec::new();
    let mut truncated = false;
    for entry in walker {
        let entry = entry.map_err(|e| e.to_string())?;
        if entry.file_type().is_symlink() {
            continue;
        }
        let is_dir = entry.file_type().is_dir();
        if dirs_only && !is_dir {
            continue;
        }
        if items.len() == max {
            truncated = true;
            break;
        }
        let meta = entry.metadata().map_err(|e| e.to_string())?;
        let modified = meta
            .modified()
            .ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_nanos());
        items.push(
            json!({"path":entry.path().to_string_lossy(),"name":entry.file_name().to_string_lossy(),
            "type":if is_dir{"directory"}else{"file"},"size":if is_dir{None}else{Some(meta.len())},
            "modified_ns":modified}),
        );
    }
    Ok(
        json!({"root":path.to_string_lossy(),"items":items,"truncated":truncated,
        "collection":result_window(items.len(),truncated)}),
    )
}
fn read(ws: &Workspace, arg: &Value) -> Result<Value, String> {
    let path = ws.path(str_arg(arg, "path")?)?;
    let encoding = arg
        .get("encoding")
        .and_then(Value::as_str)
        .unwrap_or("utf-8");
    if encoding != "utf-8" && encoding != "utf-8-sig" {
        return Err("preview supports UTF-8 only".into());
    }
    let max = integer_arg(arg, "max_chars", 50_000, 1, 1_000_000)?;
    let text = text_from_file(&path, 4_000_000)?;
    let text = text.strip_prefix('\u{feff}').unwrap_or(&text);
    let truncated = text.chars().count() > max;
    let content = text.chars().take(max).collect::<String>();
    Ok(
        json!({"path":path.to_string_lossy(),"content":content,"truncated":truncated,"encoding":encoding}),
    )
}
fn code_read(ws: &Workspace, arg: &Value) -> Result<Value, String> {
    let path = ws.path(str_arg(arg, "path")?)?;
    let text = text_from_file(&path, 4_000_000)?;
    let start = integer_arg(arg, "start_line", 1, 1, usize::MAX)?;
    let all = text.lines().collect::<Vec<_>>();
    let end = integer_arg(arg, "end_line", all.len().max(1), 1, usize::MAX)?;
    if end < start {
        return Err("end_line must be >= start_line".into());
    }
    let selected = (start..=end.min(all.len()))
        .map(|i| json!({"line":i,"text":all[i-1]}))
        .collect::<Vec<_>>();
    Ok(
        json!({"path":path.to_string_lossy(),"start_line":start,"end_line":end.min(all.len()),
        "total_lines":all.len(),"lines":selected}),
    )
}
fn code_edit(ws: &Workspace, arg: &Value) -> Result<Value, String> {
    let path = ws.path(str_arg(arg, "path")?)?;
    let old = str_arg(arg, "old_text")?;
    let replacement = str_arg(arg, "new_text")?;
    if old.is_empty() {
        return Err("old_text must not be empty".into());
    }
    let source = text_from_file(&path, 4_000_000)?;
    let count = source.matches(old).count();
    if count != 1 {
        return Err(format!(
            "code_edit requires exactly one match, found {count}"
        ));
    }
    if old == replacement {
        return Err("code_edit replacement has no effect".into());
    }
    let updated = source.replacen(old, replacement, 1);
    // Atomic replacement in same directory, preserving original on write failure.
    let mut staged = tempfile::NamedTempFile::new_in(path.parent().ok_or("missing parent")?)
        .map_err(|e| e.to_string())?;
    use std::io::Write;
    staged
        .write_all(updated.as_bytes())
        .map_err(|e| e.to_string())?;
    staged.persist(&path).map_err(|e| e.to_string())?;
    Ok(json!({"path":path.to_string_lossy(),"replacements":1,"bytes":updated.len()}))
}
fn search(ws: &Workspace, arg: &Value, content_mode: bool) -> Result<Value, String> {
    let base = ws.path(arg.get("root").and_then(Value::as_str).unwrap_or("."))?;
    if !base.exists() {
        return Err("search root not found".into());
    }
    let max = integer_arg(arg, "max_results", 200, 1, 5000)?;
    let mut results = Vec::new();
    let mut truncated = false;
    let query = str_arg(arg, if content_mode { "query" } else { "pattern" })?;
    if query.is_empty() {
        return Err("search query/pattern empty".into());
    }
    let matcher = if content_mode {
        let pattern = if arg.get("mode").and_then(Value::as_str) == Some("regex") {
            query.to_string()
        } else {
            regex::escape(query)
        };
        let regex = regex::RegexBuilder::new(&pattern)
            .case_insensitive(!bool_arg(arg, "case_sensitive", false))
            .build()
            .map_err(|e| e.to_string())?;
        Some(regex)
    } else {
        None
    };
    let glob = if !content_mode {
        Some(
            globset::Glob::new(query)
                .map_err(|e| e.to_string())?
                .compile_matcher(),
        )
    } else {
        None
    };
    let files = if base.is_file() {
        WalkDir::new(&base).max_depth(0)
    } else {
        WalkDir::new(&base)
    };
    for entry in files.follow_links(false).sort_by_file_name() {
        let entry = entry.map_err(|e| e.to_string())?;
        if entry.file_type().is_symlink() {
            continue;
        }
        let relative = entry.path().strip_prefix(&base).unwrap_or(entry.path());
        if content_mode {
            if !entry.file_type().is_file() {
                continue;
            }
            let text = match text_from_file(entry.path(), 2_000_000) {
                Ok(s) => s,
                Err(_) => continue,
            };
            let regex = matcher.as_ref().ok_or("regex unavailable")?;
            for (index, line) in text.lines().enumerate() {
                if let Some(m) = regex.find(line) {
                    if results.len() == max {
                        truncated = true;
                        break;
                    }
                    results.push(json!({"path":entry.path().to_string_lossy(),
                        "relative_path":relative.to_string_lossy(),"line":index+1,
                        "column":m.start()+1,"text":line}));
                }
            }
        } else {
            let glob = glob.as_ref().ok_or("glob unavailable")?;
            if glob.is_match(Path::new(entry.file_name())) || glob.is_match(relative) {
                if results.len() == max {
                    truncated = true;
                    break;
                }
                results.push(json!(entry.path().to_string_lossy()));
            }
        }
        if truncated {
            break;
        }
    }
    if content_mode {
        Ok(json!({"root":base.to_string_lossy(),"query":query,
            "mode":arg.get("mode").and_then(Value::as_str).unwrap_or("literal"),
            "case_sensitive":bool_arg(arg,"case_sensitive",false),
            "results":results,"truncated":truncated,"collection":result_window(results.len(),truncated)}))
    } else {
        Ok(json!({"root":base.to_string_lossy(),"pattern":query,
            "results":results,"truncated":truncated,"collection":result_window(results.len(),truncated)}))
    }
}
fn add(
    registry: &mut Registry,
    ws: &Workspace,
    name: &str,
    description: &str,
    parameters: Value,
    handler: fn(&Workspace, &Value) -> Result<Value, String>,
) -> Result<(), String> {
    let w = ws.clone();
    registry.register(Tool {
        schema: ToolSchema {
            name: name.into(),
            description: description.into(),
            parameters,
        },
        timeout: Duration::from_secs(60),
        invoke: Arc::new(move |input| {
            let w = w.clone();
            Box::pin(async move {
                tokio::task::spawn_blocking(move || handler(&w, &input))
                    .await
                    .map_err(|e| format!("workspace worker: {e}"))?
            })
        }),
    })
}
pub fn register_workspace(registry: &mut Registry, workspace: &Workspace) -> Result<(), String> {
    register_workspace_with_mode(registry,workspace,true)
}
/// Register read/search tools only; mutations are bound separately to a run-owned scope.
pub fn register_workspace_reads(registry: &mut Registry, workspace: &Workspace) -> Result<(), String> {
    register_workspace_with_mode(registry,workspace,false)
}
fn register_workspace_with_mode(registry: &mut Registry, workspace: &Workspace, include_edit: bool) -> Result<(), String> {
    add(
        registry,
        workspace,
        "file_list",
        "List files and directories in assigned workspace",
        json!({"type":"object","properties":{
            "path":{"type":"string"},"recursive":{"type":"boolean"},
            "directories_only":{"type":"boolean"},"max_items":{"type":"integer"}},
            "additionalProperties":false}),
        list,
    )?;
    add(
        registry,
        workspace,
        "file_search",
        "Search workspace names using a glob",
        json!({"type":"object","properties":{"root":{"type":"string"},
            "pattern":{"type":"string"},"max_results":{"type":"integer"}},
            "required":["pattern"],"additionalProperties":false}),
        |w, a| search(w, a, false),
    )?;
    add(
        registry,
        workspace,
        "document_read",
        "Read UTF-8 plain text file in workspace",
        json!({"type":"object","properties":{"path":{"type":"string"},"encoding":{"type":"string"},
            "max_chars":{"type":"integer"}},"required":["path"],"additionalProperties":false}),
        read,
    )?;
    add(
        registry,
        workspace,
        "code_read",
        "Read inclusive numbered lines from UTF-8 source",
        json!({"type":"object","properties":{"path":{"type":"string"},
            "start_line":{"type":"integer"},"end_line":{"type":"integer"}},
            "required":["path"],"additionalProperties":false}),
        code_read,
    )?;
    if include_edit {
    add(
        registry,
        workspace,
        "code_edit",
        "Replace exactly one literal match in existing source",
        json!({"type":"object","properties":{"path":{"type":"string"},
            "old_text":{"type":"string"},"new_text":{"type":"string"}},
            "required":["path","old_text","new_text"],"additionalProperties":false}),
        code_edit,
    )?;
    }
    add(
        registry,
        workspace,
        "code_search",
        "Search UTF-8 source contents with literal or regex query",
        json!({"type":"object","properties":{"root":{"type":"string"},
            "query":{"type":"string"},"mode":{"type":"string","enum":["literal","regex"]},
            "case_sensitive":{"type":"boolean"},"max_results":{"type":"integer"}},
            "required":["query"],"additionalProperties":false}),
        |w, a| search(w, a, true),
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (tempfile::TempDir, Workspace) {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir(dir.path().join("src")).unwrap();
        fs::write(
            dir.path().join("src/main.rs"),
            "fn alpha() {}\nfn beta() {}\n",
        )
        .unwrap();
        let ws = Workspace::new(dir.path()).unwrap();
        (dir, ws)
    }
    #[test]
    fn list_and_code_read_preserve_expected_contracts() {
        let (_dir, ws) = fixture();
        let listed = list(&ws, &json!({"path":"src","recursive":false})).unwrap();
        assert_eq!(listed["items"][0]["name"], "main.rs");
        assert_eq!(listed["collection"]["complete"], true);
        let lines = code_read(
            &ws,
            &json!({"path":"src/main.rs","start_line":2,"end_line":2}),
        )
        .unwrap();
        assert_eq!(lines["lines"][0]["line"], 2);
        assert_eq!(lines["lines"][0]["text"], "fn beta() {}");
    }
    #[test]
    fn code_edit_requires_exactly_one_occurrence_and_preserves_on_failure() {
        let (_dir, ws) = fixture();
        let path = ws.path("src/main.rs").unwrap();
        let before = fs::read_to_string(&path).unwrap();
        assert!(code_edit(
            &ws,
            &json!({"path":"src/main.rs","old_text":"fn ","new_text":"pub fn "})
        )
        .is_err());
        assert_eq!(fs::read_to_string(&path).unwrap(), before);
        let ok = code_edit(
            &ws,
            &json!({"path":"src/main.rs","old_text":"alpha","new_text":"gamma"}),
        )
        .unwrap();
        assert_eq!(ok["replacements"], 1);
        assert!(fs::read_to_string(path).unwrap().contains("gamma"));
    }
    #[test]
    fn traversal_outside_workspace_denied() {
        let (_dir, ws) = fixture();
        assert!(ws.path("../outside").is_err());
        assert!(ws.path("../../outside/new.rs").is_err());
        assert!(ws.path("src/../../../outside").is_err());
    }
    #[test]
    fn content_search_and_name_search_are_independent() {
        let (_dir, ws) = fixture();
        let c = search(
            &ws,
            &json!({"root":"src","query":"beta","max_results":10}),
            true,
        )
        .unwrap();
        assert_eq!(c["results"][0]["line"], 2);
        let n = search(&ws, &json!({"root":".","pattern":"*.rs"}), false).unwrap();
        assert_eq!(n["results"].as_array().unwrap().len(), 1);
    }
    #[test]
    fn list_reports_truncation_with_complete_false() {
        let (dir, ws) = fixture();
        fs::write(dir.path().join("src/second.rs"), "").unwrap();
        let data = list(&ws, &json!({"path":"src","max_items":1})).unwrap();
        assert_eq!(data["collection"]["complete"], false);
        assert_eq!(data["collection"]["has_more"], true);
    }
    #[test]
    fn rejects_invalid_limits_and_binary_files() {
        let (dir, ws) = fixture();
        assert!(list(&ws, &json!({"max_items":0})).is_err());
        fs::write(dir.path().join("invalid.bin"), [0xff, 0xfe]).unwrap();
        assert!(read(&ws, &json!({"path":"invalid.bin"})).is_err());
    }
}
