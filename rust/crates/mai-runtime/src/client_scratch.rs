//! Recovery for Client-owned temporary files after a prior worker crashed.
//! The Host's <workspace>/tmp/run-* must NEVER be touched by this code.
use crate::workspace_tools::Workspace;
use fs2::FileExt;
use std::{fs::{self,File,OpenOptions},path::{Path,PathBuf}};

/// Holds an OS-level exclusive advisory lock until drop; the lock file itself
/// is deliberately left in place so concurrent processes lock the same inode.
pub struct ClientScratchGuard {
    _lock: File,
    root: PathBuf,
}
impl ClientScratchGuard {
    pub fn acquire(workspace:&Workspace)->Result<Self,String>{
        let tmp=workspace.root().join("tmp");
        ensure_dir(&tmp)?;
        let root=tmp.join("client-worker");
        ensure_dir(&root)?;
        let lock_path=root.join(".worker.lock");
        if let Ok(meta)=fs::symlink_metadata(&lock_path){
            if !meta.file_type().is_file() || meta.file_type().is_symlink(){
                return Err("worker lock is not an ordinary file".into());
            }
        }
        let file=OpenOptions::new().read(true).write(true).create(true)
            .truncate(false).open(&lock_path).map_err(|e|format!("worker lock open: {e}"))?;
        file.try_lock_exclusive().map_err(|e|format!("worker scratch already in use: {e}"))?;
        let root=fs::canonicalize(&root).map_err(|e|e.to_string())?;
        if !root.starts_with(workspace.root()){
            return Err("worker scratch escapes workspace".into());
        }
        Ok(Self{_lock:file,root})
    }
    /// Only remove abandoned directories with the exact tempfile run-XXXXXX
    /// shape, inside the isolated Client folder, while holding the OS lock.
    /// No Host scratch, user file, or unknown directory is removed.
    pub fn recover_orphans(&self)->Result<usize,String>{
        let mut count=0;
        for item in fs::read_dir(&self.root).map_err(|e|e.to_string())?{
            let item=item.map_err(|e|e.to_string())?;
            let name=item.file_name();
            let Some(name)=name.to_str() else{continue};
            if !is_run_dir(name){continue}
            let kind=item.file_type().map_err(|e|e.to_string())?;
            if !kind.is_dir() || kind.is_symlink(){continue}
            let candidate=item.path();
            if candidate.parent()!=Some(self.root.as_path()){continue}
            fs::remove_dir_all(&candidate)
                .map_err(|e|format!("recover abandoned scratch {name}: {e}"))?;
            count+=1;
        }
        Ok(count)
    }
    pub fn root(&self)->&Path{&self.root}
}
fn ensure_dir(path:&Path)->Result<(),String>{
    match fs::symlink_metadata(path){
        Ok(meta) if meta.is_dir()&&!meta.file_type().is_symlink()=>Ok(()),
        Ok(_)=>Err(format!("unsafe scratch directory: {}",path.display())),
        Err(e) if e.kind()==std::io::ErrorKind::NotFound=>{
            fs::create_dir(path).map_err(|e|e.to_string())?;
            Ok(())
        }
        Err(e)=>Err(e.to_string()),
    }
}
fn is_run_dir(name:&str)->bool{
    name.len()==10 && name.starts_with("run-")
        && name[4..].bytes().all(|b|b.is_ascii_alphanumeric())
}
#[cfg(test)]
mod tests{
    use super::*;
    #[test]
    fn recovery_is_isolated_and_preserves_user_and_host_files(){
        let folder=tempfile::tempdir().unwrap();
        let ws=Workspace::new(folder.path()).unwrap();
        let guard=ClientScratchGuard::acquire(&ws).unwrap();
        let host=folder.path().join("tmp/run-AAAAAA");
        fs::create_dir(&host).unwrap();
        fs::write(host.join("host.txt"),"host").unwrap();
        fs::write(guard.root().join("notes.txt"),"user").unwrap();
        fs::create_dir(guard.root().join("run-ABC123")).unwrap();
        fs::write(guard.root().join("run-ABC123/stale.txt"),"stale").unwrap();
        fs::create_dir(guard.root().join("run-custom-long")).unwrap();
        assert_eq!(guard.recover_orphans().unwrap(),1);
        assert_eq!(guard.recover_orphans().unwrap(),0);
        assert!(host.join("host.txt").exists());
        assert!(guard.root().join("notes.txt").exists());
        assert!(guard.root().join("run-custom-long").exists());
        assert!(!guard.root().join("run-ABC123").exists());
    }
    #[test]
    fn concurrent_workers_cannot_recover_same_scratch(){
        let folder=tempfile::tempdir().unwrap();
        let ws=Workspace::new(folder.path()).unwrap();
        let first=ClientScratchGuard::acquire(&ws).unwrap();
        assert!(ClientScratchGuard::acquire(&ws).is_err());
        drop(first);
        assert!(ClientScratchGuard::acquire(&ws).is_ok());
    }
}
