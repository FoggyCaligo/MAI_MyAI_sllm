//! Single-resident router guard. Hold the returned lock across inference.
//! Cross-process callers require the existing shared llama.cpp gateway.
use reqwest::Client;
use serde_json::{json, Value};
use std::{sync::Arc, time::Duration};
use sysinfo::System;
use tokio::{
    sync::{Mutex, MutexGuard},
    time::{sleep, Instant},
};
use tokio_util::sync::CancellationToken;
const MIB: u64 = 1_048_576;
pub trait RamProbe: Send + Sync {
    fn available_bytes(&self) -> Option<u64>;
}
pub struct PhysicalRam;
impl RamProbe for PhysicalRam {
    fn available_bytes(&self) -> Option<u64> {
        let mut s = System::new();
        s.refresh_memory();
        let n = s.available_memory();
        (n > 0).then_some(n)
    }
}
#[derive(Clone, Debug)]
pub struct Policy {
    pub host: String,
    pub min_ram_mib: u64,
    pub min_vram_mib: u64,
    pub unload_timeout: Duration,
    pub load_timeout: Duration,
    pub poll: Duration,
}
impl Policy {
    pub fn new(host: impl Into<String>) -> Self {
        Self {
            host: host.into(),
            min_ram_mib: 6144,
            min_vram_mib: 1024,
            unload_timeout: Duration::from_secs(30),
            load_timeout: Duration::from_secs(180),
            poll: Duration::from_millis(250),
        }
    }
}
pub struct ModelSupervisor {
    client: Client,
    host: String,
    policy: Policy,
    ram: Arc<dyn RamProbe>,
    gate: Mutex<()>,
}
impl ModelSupervisor {
    pub fn new(policy: Policy, ram: Arc<dyn RamProbe>) -> Result<Self, String> {
        let u = reqwest::Url::parse(&policy.host).map_err(|e| e.to_string())?;
        if !matches!(u.scheme(), "http" | "https")
            || u.query().is_some()
            || u.fragment().is_some()
            || policy.min_ram_mib == 0
            || policy.min_vram_mib == 0
            || policy.poll.is_zero()
            || policy.unload_timeout.is_zero()
            || policy.load_timeout.is_zero()
        {
            return Err("invalid router policy".into());
        }
        let client = Client::builder()
            .connect_timeout(Duration::from_secs(5))
            .build()
            .map_err(|e| e.to_string())?;
        Ok(Self {
            client,
            host: policy.host.trim_end_matches('/').into(),
            policy,
            ram,
            gate: Mutex::new(()),
        })
    }
    pub fn check_ram(&self) -> Result<(), String> {
        let free = self
            .ram
            .available_bytes()
            .ok_or("RAM measurement unavailable")?;
        let min = self
            .policy
            .min_ram_mib
            .checked_mul(MIB)
            .ok_or("RAM threshold overflow")?;
        if free < min {
            return Err(format!(
                "available RAM below safe minimum: {} MiB < {} MiB",
                free / MIB,
                self.policy.min_ram_mib
            ));
        }
        Ok(())
    }
    async fn catalog(&self) -> Result<Vec<Value>, String> {
        let r = self
            .client
            .get(format!("{}/models", self.host))
            .timeout(Duration::from_secs(5))
            .send()
            .await
            .map_err(|e| e.to_string())?;
        if !r.status().is_success() {
            return Err(format!("router status HTTP {}", r.status()));
        }
        let data = r.json::<Value>().await.map_err(|e| e.to_string())?;
        let entries = data
            .get("data")
            .and_then(Value::as_array)
            .ok_or("missing router data array")?;
        for v in entries {
            if v.get("id").and_then(Value::as_str).is_none()
                || v.pointer("/status/value").and_then(Value::as_str).is_none()
            {
                return Err("router model ID/status missing".into());
            }
        }
        Ok(entries.clone())
    }
    async fn post(&self, path: &str, model: &str) -> Result<(), String> {
        let r = self
            .client
            .post(format!("{}{}", self.host, path))
            .timeout(Duration::from_secs(15))
            .json(&json!({"model":model}))
            .send()
            .await
            .map_err(|e| e.to_string())?;
        if !r.status().is_success() {
            return Err(format!("{path} HTTP {}", r.status()));
        }
        let data = r.json::<Value>().await.map_err(|e| e.to_string())?;
        if data.get("success") != Some(&Value::Bool(true)) {
            return Err(format!("{path} rejected: {model}"));
        }
        Ok(())
    }
    async fn wait_status(
        &self,
        model: &str,
        want: &str,
        limit: Duration,
        cancel: &CancellationToken,
        retry_transport: bool,
    ) -> Result<(), String> {
        let deadline = Instant::now() + limit;
        loop {
            if cancel.is_cancelled() {
                return Err("transition cancelled".into());
            }
            let state = match self.catalog().await {
                Ok(list) => list
                    .into_iter()
                    .find(|v| v.get("id").and_then(Value::as_str) == Some(model))
                    .ok_or("model disappeared from router")?,
                Err(_) if retry_transport && Instant::now() < deadline => {
                    sleep(self.policy.poll).await;
                    continue;
                }
                Err(e) => return Err(e),
            };
            let status = state
                .pointer("/status/value")
                .and_then(Value::as_str)
                .ok_or("missing status")?;
            if status == want {
                return Ok(());
            }
            if state.pointer("/status/failed").and_then(Value::as_bool) == Some(true) {
                return Err(format!("router model failure: {model}"));
            }
            if !matches!(status, "loaded" | "unloaded" | "loading" | "sleeping") {
                return Err(format!("unknown model state: {status}"));
            }
            if Instant::now() >= deadline {
                return Err(format!("{model} did not reach {want} (state={status})"));
            }
            tokio::select! {
                _=cancel.cancelled()=>return Err("transition cancelled".into()),
                _=sleep(self.policy.poll)=>{}
            }
        }
    }
    pub fn validate_vram(&self, model: &Value) -> Result<(), String> {
        let mem = model.get("memory").ok_or("GPU memory estimate missing")?;
        if mem.get("status").and_then(Value::as_str) != Some("measured") {
            return Err("GPU memory estimate not measured".into());
        }
        let devices = mem
            .pointer("/max/devices")
            .and_then(Value::as_array)
            .ok_or("GPU per-device estimate missing")?;
        let mut found = false;
        for d in devices {
            if d.get("kind").and_then(Value::as_str) != Some("device") {
                continue;
            }
            found = true;
            let n = |key: &str| {
                d.get(key)
                    .and_then(Value::as_u64)
                    .ok_or_else(|| format!("GPU field missing/invalid: {key}"))
            };
            let needed = n("model")?
                .checked_add(n("context")?)
                .and_then(|x| x.checked_add(n("compute").ok()?))
                .and_then(|x| x.checked_add(1024 * MIB))
                .and_then(|x| x.checked_add(self.policy.min_vram_mib.checked_mul(MIB)?))
                .ok_or("GPU estimate overflow")?;
            if n("free")? < needed {
                return Err("GPU free memory safety margin exceeded".into());
            }
        }
        if !found {
            return Err("no measured GPU device".into());
        }
        Ok(())
    }
    async fn ensure(&self, model: &str, cancel: &CancellationToken) -> Result<(), String> {
        self.check_ram()?;
        let before = self.catalog().await?;
        if !before
            .iter()
            .any(|v| v.get("id").and_then(Value::as_str) == Some(model))
        {
            return Err(format!("unknown router model: {model}"));
        }
        for v in &before {
            let id = v
                .get("id")
                .and_then(Value::as_str)
                .ok_or("missing model ID")?;
            let state = v
                .pointer("/status/value")
                .and_then(Value::as_str)
                .ok_or("missing status")?;
            if !matches!(state, "loaded" | "unloaded" | "loading" | "sleeping") {
                return Err(format!("unknown router state: {state}"));
            }
            if id != model && state != "unloaded" {
                self.post("/models/unload", id).await?;
                self.wait_status(id, "unloaded", self.policy.unload_timeout, cancel, false)
                    .await?;
            }
        }
        let after = self.catalog().await?;
        if after.iter().any(|v| {
            v.get("id").and_then(Value::as_str) != Some(model)
                && v.pointer("/status/value").and_then(Value::as_str) != Some("unloaded")
        }) {
            return Err("another resident model remains loaded".into());
        }
        let current = after
            .iter()
            .find(|v| v.get("id").and_then(Value::as_str) == Some(model))
            .ok_or("target model missing")?;
        if current.pointer("/status/value").and_then(Value::as_str) != Some("loaded") {
            let deadline = Instant::now() + Duration::from_secs(30);
            loop {
                let list = self.catalog().await?;
                let info = list
                    .iter()
                    .find(|v| v.get("id").and_then(Value::as_str) == Some(model))
                    .ok_or("model memory metadata missing")?;
                match info.pointer("/memory/status").and_then(Value::as_str) {
                    Some("measured") => {
                        self.validate_vram(info)?;
                        break;
                    }
                    Some("failed") => return Err("GPU measurement failed".into()),
                    _ if Instant::now() >= deadline => {
                        return Err("GPU measurement timed out".into())
                    }
                    _ => {}
                }
                tokio::select! {
                    _=cancel.cancelled()=>return Err("transition cancelled".into()),
                    _=sleep(self.policy.poll)=>{}
                }
            }
            self.post("/models/load", model).await?;
            self.wait_status(model, "loaded", self.policy.load_timeout, cancel, true)
                .await?;
        }
        self.check_ram()?;
        Ok(())
    }
    /// Keep guard alive during the entire chat/tool loop.
    pub async fn acquire<'a>(
        &'a self,
        model: &str,
        cancel: &CancellationToken,
    ) -> Result<MutexGuard<'a, ()>, String> {
        if model.trim().is_empty() {
            return Err("missing model ID".into());
        }
        let guard = tokio::select! {
            _=cancel.cancelled()=>return Err("transition cancelled".into()),
            guard=self.gate.lock()=>guard
        };
        self.ensure(model, cancel).await?;
        if cancel.is_cancelled() {
            return Err("transition cancelled".into());
        }
        Ok(guard)
    }
}
