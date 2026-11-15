//! Authenticated remote-worker session relay for the Rust preview.
//! Mirrors the native.invoke/native.cleanup JSON wire contract of the Python
//! worker. The Rust preview is loopback-only; a TLS gateway is required for a
//! worker on another machine. No credentials are sent to the model.
use mai_runtime::{workspace_route::RemoteTransport, BoxFuture};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::{mpsc, oneshot, Mutex};
use uuid::Uuid;

const LEASE: Duration = Duration::from_secs(180);
const POLL_WAIT: Duration = Duration::from_secs(15);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(600);
const CLEANUP_TIMEOUT: Duration = Duration::from_secs(30);

struct Session {
    owner: String,
    secret: String,
    connected: bool,
    last_seen: Instant,
    tx: mpsc::Sender<Value>,
    rx: Arc<Mutex<mpsc::Receiver<Value>>>,
    pending: HashMap<String, oneshot::Sender<Result<Value, String>>>,
}
#[derive(Default)]
pub struct RemoteHub {
    sessions: Mutex<HashMap<String, Session>>,
}
impl RemoteHub {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }
    pub async fn create(&self, owner: &str) -> Result<Value, String> {
        let mut all = self.sessions.lock().await;
        if all.values().any(|s| s.owner == owner) {
            return Err("owner already has a remote worker session".into());
        }
        let id = Uuid::new_v4().to_string();
        let secret = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
        let (tx, rx) = mpsc::channel(32);
        all.insert(
            id.clone(),
            Session {
                owner: owner.to_string(),
                secret: secret.clone(),
                connected: false,
                last_seen: Instant::now(),
                tx,
                rx: Arc::new(Mutex::new(rx)),
                pending: HashMap::new(),
            },
        );
        Ok(json!({"session_id":id,"secret":secret}))
    }
    pub async fn revoke_owner(&self, owner: &str, id: &str) -> Result<(), String> {
        let mut all = self.sessions.lock().await;
        if !all.get(id).is_some_and(|session| session.owner == owner) {
            return Err("remote session is not owned by this account".into());
        }
        if let Some(session) = all.remove(id) {
            for (_, sender) in session.pending {
                let _ = sender.send(Err("remote session revoked by owner".into()));
            }
        }
        Ok(())
    }
    pub async fn belongs_to(&self, owner: &str, id: &str) -> bool {
        let all = self.sessions.lock().await;
        all.get(id).is_some_and(|s| s.owner == owner)
    }
    pub async fn poll(&self, id: &str, secret: &str) -> Result<Value, String> {
        let rx = {
            let mut all = self.sessions.lock().await;
            let s = authenticate(&mut all, id, secret)?;
            s.connected = true;
            s.last_seen = Instant::now();
            s.rx.clone()
        };
        // Dropping a poll request must not remove the receiver from the hub.
        let received = {
            let mut receiver = rx.lock().await;
            tokio::time::timeout(POLL_WAIT, receiver.recv()).await
        };
        let mut all = self.sessions.lock().await;
        let s = authenticate(&mut all, id, secret)?;
        s.last_seen = Instant::now();
        Ok(json!({"request":match received{Ok(Some(value))=>Some(value),_=>None}}))
    }
    pub async fn resolve(&self, id: &str, secret: &str, payload: Value) -> Result<(), String> {
        let request_id = payload
            .get("request_id")
            .and_then(Value::as_str)
            .filter(|v| !v.is_empty())
            .ok_or("invalid request ID")?;
        let ok = payload
            .get("ok")
            .and_then(Value::as_bool)
            .ok_or("missing ok field")?;
        let mut all = self.sessions.lock().await;
        let session = authenticate(&mut all, id, secret)?;
        session.last_seen = Instant::now();
        let sender = session
            .pending
            .remove(request_id)
            .ok_or("unknown or already resolved request ID")?;
        let response = if ok {
            let result = payload
                .get("result")
                .ok_or("successful result missing envelope")?;
            let obj = result.as_object().ok_or("result envelope must be object")?;
            if obj.len() != 1 || !obj.contains_key("value") {
                return Err("invalid native tool response envelope".into());
            }
            Ok(result.clone())
        } else {
            let error = payload
                .get("error")
                .and_then(Value::as_str)
                .unwrap_or("unknown worker error");
            Err(format!("remote worker error: {error}"))
        };
        let _ = sender.send(response);
        Ok(())
    }
    pub async fn revoke(&self, id: &str, secret: &str) -> Result<(), String> {
        let mut all = self.sessions.lock().await;
        authenticate(&mut all, id, secret)?;
        if let Some(session) = all.remove(id) {
            for (_, sender) in session.pending {
                let _ = sender.send(Err("remote worker disconnected".into()));
            }
        }
        Ok(())
    }
}
fn authenticate<'a>(
    all: &'a mut HashMap<String, Session>,
    id: &str,
    secret: &str,
) -> Result<&'a mut Session, String> {
    let s = all.get_mut(id).ok_or("remote session missing")?;
    if s.secret != secret {
        return Err("worker authentication failed".into());
    }
    Ok(s)
}
impl RemoteTransport for RemoteHub {
    fn connected<'a>(
        &'a self,
        owner: &'a str,
        session_id: &'a str,
    ) -> BoxFuture<'a, Result<bool, String>> {
        Box::pin(async move {
            let all = self.sessions.lock().await;
            Ok(all
                .get(session_id)
                .is_some_and(|s| s.owner == owner && s.connected && s.last_seen.elapsed() < LEASE))
        })
    }
    fn request<'a>(
        &'a self,
        owner: &'a str,
        session_id: &'a str,
        operation: &'a str,
        args: Value,
    ) -> BoxFuture<'a, Result<Value, String>> {
        Box::pin(async move {
            let (sender, rx) = {
                let mut all = self.sessions.lock().await;
                let session = all.get_mut(session_id).ok_or("remote session missing")?;
                if session.owner != owner {
                    return Err("remote session belongs to another owner".into());
                }
                if !session.connected || session.last_seen.elapsed() >= LEASE {
                    return Err("remote worker disconnected or lease expired".into());
                }
                let id = Uuid::new_v4().to_string();
                let (tx, rx) = oneshot::channel();
                session.pending.insert(id.clone(), tx);
                ((session.tx.clone(), id), rx)
            };
            let (tx, request_id) = sender;
            // Sending into bounded queue must not wait unboundedly if worker stalls.
            let request = json!({"request_id":request_id,"operation":operation,"arguments":args});
            if !matches!(
                tokio::time::timeout(Duration::from_secs(5), tx.send(request)).await,
                Ok(Ok(()))
            ) {
                self.sessions
                    .lock()
                    .await
                    .get_mut(session_id)
                    .map(|s| s.pending.remove(&request_id));
                return Err("remote worker queue backpressure".into());
            }
            let deadline = if operation == "native.cleanup" {
                CLEANUP_TIMEOUT
            } else {
                REQUEST_TIMEOUT
            };
            let outcome = tokio::time::timeout(deadline, rx).await;
            if !matches!(outcome, Ok(Ok(_))) {
                self.sessions
                    .lock()
                    .await
                    .get_mut(session_id)
                    .map(|s| s.pending.remove(&request_id));
            }
            match outcome {
                Ok(Ok(result)) => result,
                Ok(Err(_)) => Err("remote worker response channel closed".into()),
                Err(_) => Err("remote worker response timeout".into()),
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn owner_can_revoke_stale_session_and_create_replacement() {
        let hub = RemoteHub::new();
        let original = hub.create("alice").await.unwrap();
        let id = original["session_id"].as_str().unwrap();
        assert!(hub.revoke_owner("bob", id).await.is_err());
        assert!(hub.create("alice").await.is_err());
        hub.revoke_owner("alice", id).await.unwrap();
        assert!(hub.create("alice").await.is_ok());
    }
    #[tokio::test]
    async fn owner_scope_and_result_roundtrip() {
        let hub = RemoteHub::new();
        let token = hub.create("owner").await.unwrap();
        let id = token["session_id"].as_str().unwrap().to_string();
        let secret = token["secret"].as_str().unwrap().to_string();
        assert!(!hub.connected("owner", &id).await.unwrap());
        assert!(hub.create("owner").await.is_err());
        assert!(hub.poll(&id, "wrong").await.is_err());
        let other = hub.clone();
        let sid = id.clone();
        let key = secret.clone();
        let poll = tokio::spawn(async move { other.poll(&sid, &key).await.unwrap() });
        tokio::task::yield_now().await;
        let mut ready = false;
        for _ in 0..50 {
            if hub.connected("owner", &id).await.unwrap() {
                ready = true;
                break;
            }
            tokio::task::yield_now().await;
        }
        assert!(ready);
        let other = hub.clone();
        let sid = id.clone();
        let call = tokio::spawn(async move {
            other
                .request(
                    "owner",
                    &sid,
                    "native.invoke",
                    json!({"name":"terminal_run"}),
                )
                .await
        });
        let item = poll.await.unwrap();
        let task = item["request"].clone();
        assert_eq!(task["operation"], "native.invoke");
        let request_id = task["request_id"].as_str().unwrap();
        hub.resolve(
            &id,
            &secret,
            json!({"request_id":request_id,"ok":true,
            "result":{"value":{"stdout":"good"}}}),
        )
        .await
        .unwrap();
        assert_eq!(call.await.unwrap().unwrap()["value"]["stdout"], "good");
        assert!(!hub.connected("intruder", &id).await.unwrap());
        assert!(hub
            .request("intruder", &id, "native.cleanup", json!({}))
            .await
            .is_err());
        hub.revoke(&id, &secret).await.unwrap();
        assert!(!hub.connected("owner", &id).await.unwrap());
    }
}
