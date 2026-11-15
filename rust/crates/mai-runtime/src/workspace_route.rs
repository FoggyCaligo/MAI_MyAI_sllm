//! Host/client execution routing contract. No MCP dependency.
//! The target is a per-run state machine. A failed client switch is atomic,
//! and a disconnected client never falls back to host.
use crate::{BoxFuture, Registry, Tool, ToolFn, ToolSchema};
use serde_json::{json, Value};
use std::{collections::BTreeSet, sync::Arc, time::Duration};
use tokio::sync::Mutex;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Target {
    Host,
    Client { session_id: String },
}
/// Implemented by the authenticated worker-session transport, not the tool.
pub trait RemoteTransport: Send + Sync {
    fn connected<'a>(
        &'a self,
        owner: &'a str,
        session_id: &'a str,
    ) -> BoxFuture<'a, Result<bool, String>>;
    fn request<'a>(
        &'a self,
        owner: &'a str,
        session_id: &'a str,
        operation: &'a str,
        args: Value,
    ) -> BoxFuture<'a, Result<Value, String>>;
}
struct State {
    selected: Target,
    used: BTreeSet<String>,
    closed: bool,
}
pub struct WorkspaceRoute {
    owner: String,
    run_id: String,
    client_session_id: Option<String>,
    transport: Arc<dyn RemoteTransport>,
    state: Mutex<State>,
}
impl WorkspaceRoute {
    pub fn new(
        owner: String,
        run_id: String,
        client_session_id: Option<String>,
        transport: Arc<dyn RemoteTransport>,
    ) -> Result<Self, String> {
        if owner.trim().is_empty() || run_id.trim().is_empty() {
            return Err("owner and run ID are required".into());
        }
        if client_session_id.as_ref().is_some_and(|s| s.is_empty()) {
            return Err("empty remote session ID".into());
        }
        Ok(Self {
            owner,
            run_id,
            client_session_id,
            transport,
            state: Mutex::new(State {
                selected: Target::Host,
                used: BTreeSet::new(),
                closed: false,
            }),
        })
    }
    pub async fn snapshot(&self) -> Target {
        self.state.lock().await.selected.clone()
    }
    pub async fn switch(&self, target: &str) -> Result<Value, String> {
        match target {
            "host" => {
                let mut state = self.state.lock().await;
                if state.closed {
                    return Err("workspace run already closed".into());
                }
                let changed = state.selected != Target::Host;
                state.selected = Target::Host;
                Ok(json!({"ok":true,"workspace":"host","changed":changed,
                    "message":if changed{"switched to host"}else{"이미 host에 있습니다."}}))
            }
            "client" => {
                // Never hold state mutex while awaiting network I/O.
                let session = self
                    .client_session_id
                    .as_deref()
                    .ok_or("No authenticated connected remote worker for this owner")?;
                if !self.transport.connected(&self.owner, session).await? {
                    return Err("No authenticated connected remote worker for this owner".into());
                }
                let mut state = self.state.lock().await;
                if state.closed {
                    return Err("workspace run already closed".into());
                }
                let chosen = Target::Client {
                    session_id: session.to_string(),
                };
                let changed = state.selected != chosen;
                state.selected = chosen;
                state.used.insert(session.to_string());
                Ok(json!({"ok":true,"workspace":"client","changed":changed,
                    "message":if changed{"switched to client"}else{"이미 client에 있습니다."}}))
            }
            _ => Err("workspace target must be host or client".into()),
        }
    }
    /// Snapshot before the first await. Later switch calls do not move this invocation.
    pub async fn execute(&self, name: &str, args: Value, local: &ToolFn) -> Result<Value, String> {
        let target = {
            let mut state = self.state.lock().await;
            if state.closed {
                return Err("workspace run already closed".into());
            }
            let snapshot = state.selected.clone();
            if let Target::Client { session_id } = &snapshot {
                state.used.insert(session_id.clone());
            }
            snapshot
        };
        match target {
            Target::Host => (local)(args).await,
            Target::Client { session_id } => {
                if !self.transport.connected(&self.owner, &session_id).await? {
                    return Err(
                        "Selected client worker disconnected; refusing host fallback".into(),
                    );
                }
                let reply = self
                    .transport
                    .request(
                        &self.owner,
                        &session_id,
                        "native.invoke",
                        json!({"name":name,"arguments":args,"run_id":self.run_id}),
                    )
                    .await?;
                let object = reply
                    .as_object()
                    .ok_or("remote result must be an object envelope")?;
                if object.len() != 1 || !object.contains_key("value") {
                    return Err("remote result must contain exactly a value field".into());
                }
                Ok(object["value"].clone())
            }
        }
    }
    /// Must be called after all tool invocations have completed and before reporting
    /// a successful final answer. Remote cleanup failure must be surfaced to caller.
    pub async fn cleanup(&self) -> Result<(), String> {
        let used = {
            let mut state = self.state.lock().await;
            if state.closed {
                return Ok(());
            }
            state.closed = true;
            state.used.iter().cloned().collect::<Vec<_>>()
        };
        let mut failures = Vec::new();
        for session_id in used {
            let outcome = self
                .transport
                .request(
                    &self.owner,
                    &session_id,
                    "native.cleanup",
                    json!({"run_id":self.run_id}),
                )
                .await;
            match outcome {
                Ok(value)
                    if value.as_object().is_some_and(|v| {
                        v.len() == 1
                            && v.get("value")
                                .is_some_and(|x| x.get("cleaned") == Some(&Value::Bool(true)))
                    }) => {}
                Ok(_) => failures.push(format!("{session_id}: invalid cleanup acknowledgement")),
                Err(e) => failures.push(format!("{session_id}: {e}")),
            }
        }
        if failures.is_empty() {
            Ok(())
        } else {
            Err(format!(
                "Remote worker scratch cleanup not confirmed: {}",
                failures.join("; ")
            ))
        }
    }
}
pub fn register_switch(
    registry: &mut Registry,
    workspace: Arc<WorkspaceRoute>,
) -> Result<(), String> {
    registry.register(Tool{
        schema:ToolSchema{name:"switch_workspace".into(),
            description:"Set persistent tool execution location for this agent run. host or client; client requires authenticated connected owner worker. Repeating target is a no-op.".into(),
            parameters:json!({"type":"object","properties":{"target":{"type":"string",
                "enum":["host","client"]}},"required":["target"],"additionalProperties":false})},
        timeout:Duration::from_secs(30),
        invoke:Arc::new(move |args| {
            let state=workspace.clone();
            Box::pin(async move{
                let target=args.get("target").and_then(Value::as_str).ok_or("missing target")?;
                state.switch(target).await
            })
        }),
    })
}
pub fn route_tool(tool: Tool, workspace: Arc<WorkspaceRoute>) -> Tool {
    let Tool {
        schema,
        invoke,
        timeout,
    } = tool;
    let name = schema.name.clone();
    Tool {
        schema,
        timeout: timeout.max(Duration::from_secs(600)),
        invoke: Arc::new(move |args| {
            let workspace = workspace.clone();
            let local = invoke.clone();
            let name = name.clone();
            Box::pin(async move { workspace.execute(&name, args, &local).await })
        }),
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    struct Fake {
        online: AtomicBool,
        calls: AtomicUsize,
        cleanup_ok: AtomicBool,
    }
    impl RemoteTransport for Fake {
        fn connected<'a>(
            &'a self,
            owner: &'a str,
            session: &'a str,
        ) -> BoxFuture<'a, Result<bool, String>> {
            Box::pin(async move {
                Ok(owner == "owner" && session == "worker" && self.online.load(Ordering::SeqCst))
            })
        }
        fn request<'a>(
            &'a self,
            _owner: &'a str,
            _session: &'a str,
            operation: &'a str,
            args: Value,
        ) -> BoxFuture<'a, Result<Value, String>> {
            Box::pin(async move {
                self.calls.fetch_add(1, Ordering::SeqCst);
                match operation {
                    "native.invoke" => Ok(json!({"value":{"location":"client","args":args}})),
                    "native.cleanup" => {
                        Ok(json!({"value":{"cleaned":self.cleanup_ok.load(Ordering::SeqCst)}}))
                    }
                    _ => Err("unexpected operation".into()),
                }
            })
        }
    }
    fn fixture() -> (Arc<Fake>, WorkspaceRoute, Arc<AtomicUsize>) {
        let transport = Arc::new(Fake {
            online: AtomicBool::new(true),
            calls: AtomicUsize::new(0),
            cleanup_ok: AtomicBool::new(true),
        });
        let local = Arc::new(AtomicUsize::new(0));
        let route = WorkspaceRoute::new(
            "owner".into(),
            "run-id".into(),
            Some("worker".into()),
            transport.clone(),
        )
        .unwrap();
        (transport, route, local)
    }
    fn local(counter: Arc<AtomicUsize>) -> ToolFn {
        Arc::new(move |_| {
            let c = counter.clone();
            Box::pin(async move {
                c.fetch_add(1, Ordering::SeqCst);
                Ok(json!({"location":"host"}))
            })
        })
    }
    #[tokio::test]
    async fn switching_changes_all_followup_invocations_and_repeating_is_noop() {
        let (transport, route, counter) = fixture();
        assert_eq!(
            route
                .execute("terminal_run", json!({}), &local(counter.clone()))
                .await
                .unwrap()["location"],
            "host"
        );
        assert_eq!(route.switch("client").await.unwrap()["changed"], true);
        assert_eq!(route.switch("client").await.unwrap()["changed"], false);
        let answer = route
            .execute(
                "terminal_run",
                json!({"command":"pwd"}),
                &local(counter.clone()),
            )
            .await
            .unwrap();
        assert_eq!(answer["location"], "client");
        assert_eq!(answer["args"]["name"], "terminal_run");
        assert_eq!(answer["args"]["run_id"], "run-id");
        route.switch("host").await.unwrap();
        assert_eq!(
            route
                .execute("terminal_run", json!({}), &local(counter.clone()))
                .await
                .unwrap()["location"],
            "host"
        );
        assert_eq!(counter.load(Ordering::SeqCst), 2);
        route.cleanup().await.unwrap();
        assert_eq!(transport.calls.load(Ordering::SeqCst), 2);
    }
    #[tokio::test]
    async fn disconnected_worker_never_falls_back_to_host() {
        let (transport, route, counter) = fixture();
        transport.online.store(false, Ordering::SeqCst);
        assert!(route.switch("client").await.is_err());
        assert_eq!(route.snapshot().await, Target::Host);
        transport.online.store(true, Ordering::SeqCst);
        route.switch("client").await.unwrap();
        transport.online.store(false, Ordering::SeqCst);
        assert!(route
            .execute("terminal_run", json!({}), &local(counter.clone()))
            .await
            .is_err());
        assert_eq!(counter.load(Ordering::SeqCst), 0);
        assert_eq!(
            route.snapshot().await,
            Target::Client {
                session_id: "worker".into()
            }
        );
    }
    #[tokio::test]
    async fn cleanup_failure_is_not_silent_and_closes_run() {
        let (transport, route, counter) = fixture();
        route.switch("client").await.unwrap();
        transport.cleanup_ok.store(false, Ordering::SeqCst);
        assert!(route.cleanup().await.unwrap_err().contains("not confirmed"));
        assert!(route
            .execute("terminal_run", json!({}), &local(counter))
            .await
            .is_err());
    }
    #[tokio::test]
    async fn absent_client_does_not_change_target() {
        let transport = Arc::new(Fake {
            online: AtomicBool::new(true),
            calls: AtomicUsize::new(0),
            cleanup_ok: AtomicBool::new(true),
        });
        let route = WorkspaceRoute::new("owner".into(), "job".into(), None, transport).unwrap();
        assert!(route.switch("client").await.is_err());
        assert_eq!(route.snapshot().await, Target::Host);
        route.cleanup().await.unwrap();
    }
}
