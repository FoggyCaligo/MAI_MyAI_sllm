//! OpenAI-compatible llama.cpp chat adapter.
//! Deliberately uses the existing llama.cpp HTTP process, not libllama FFI.
//! The selected model MUST be loaded by the future ModelSupervisor first.
//! Non-streaming initial integration; token streaming comes later.
use crate::{BoxFuture, Call, Message, Model, ModelStep, Role, ToolSchema};
use reqwest::{Client, Url};
use serde_json::{json, Value};
use std::time::Duration;

#[derive(Clone, Debug)]
pub struct LlamaConfig {
    pub endpoint: String,
    pub model: String,
    pub max_output_tokens: u32,
}

pub struct LlamaCpp {
    client: Client,
    url: Url,
    config: LlamaConfig,
    wire: Vec<Value>,
    consumed: usize,
    pending: Vec<(String, String)>,
    turn_index: u64,
}

impl LlamaCpp {
    pub fn new(config: LlamaConfig) -> Result<Self, String> {
        if config.model.trim().is_empty() || config.max_output_tokens == 0 {
            return Err("model and max_output_tokens must be nonempty".into());
        }
        let host = Url::parse(&config.endpoint).map_err(|e| format!("invalid endpoint: {e}"))?;
        if !matches!(host.scheme(), "http" | "https") || host.query().is_some() || host.fragment().is_some() {
            return Err("endpoint must be an HTTP(S) base URL without query/fragment".into());
        }
        let url = host.join("v1/chat/completions").map_err(|e| e.to_string())?;
        let client = Client::builder()
            .connect_timeout(Duration::from_secs(10))
            .build()
            .map_err(|e| format!("HTTP client init: {e}"))?;
        Ok(Self { client, url, config, wire: vec![], consumed: 0, pending: vec![], turn_index: 0 })
    }

    fn accept_history(&mut self, history: &[Message]) -> Result<(), String> {
        if history.len() < self.consumed {
            return Err("history was truncated while model had pending calls".into());
        }
        let next = &history[self.consumed..];
        if self.consumed == 0 {
            if next.len() != 1 || next[0].role != Role::User {
                return Err("initial model history must start with one user message".into());
            }
            self.wire.push(json!({"role":"user","content":next[0].content}));
        } else {
            if next.len() != self.pending.len() {
                return Err("assistant tool-call count does not match returned tool results".into());
            }
            for (message, (name, id)) in next.iter().zip(self.pending.iter()) {
                if message.role != Role::Tool {
                    return Err("expected tool result after assistant tool calls".into());
                }
                let envelope: Value = serde_json::from_str(&message.content)
                    .map_err(|_| "malformed internal tool result JSON".to_string())?;
                if envelope.get("name").and_then(Value::as_str) != Some(name.as_str()) {
                    return Err("tool result name does not match previous assistant call".into());
                }
                self.wire.push(json!({
                    "role":"tool",
                    "tool_call_id":id,
                    "content":message.content
                }));
            }
            self.pending.clear();
        }
        self.consumed = history.len();
        Ok(())
    }

    async fn complete(
        &mut self, history: &[Message], available: &[ToolSchema],
    ) -> Result<ModelStep, String> {
        self.accept_history(history)?;
        let tools: Vec<Value> = available.iter().map(|tool| json!({
            "type":"function",
            "function":{
                "name":tool.name,
                "description":tool.description,
                "parameters":tool.parameters
            }
        })).collect();
        let body = json!({
            "model": self.config.model,
            "messages": self.wire,
            "tools": tools,
            "stream":false,
            "cache_prompt":true,
            "max_tokens":self.config.max_output_tokens
        });
        let response = self.client.post(self.url.clone()).json(&body)
            .send().await.map_err(|e| format!("llama.cpp HTTP request: {e}"))?;
        let status = response.status();
        if !status.is_success() {
            let body = response.text().await.unwrap_or_default();
            return Err(format!("llama.cpp HTTP {status}: {}", body.chars().take(512).collect::<String>()));
        }
        let data: Value = response.json().await.map_err(|e| format!("llama.cpp JSON response: {e}"))?;
        let choice = data.get("choices").and_then(Value::as_array)
            .and_then(|items| items.first()).ok_or("llama.cpp missing choices")?;
        if choice.get("finish_reason").and_then(Value::as_str) == Some("length") {
            return Err("llama.cpp truncated output (finish_reason=length)".into());
        }
        let assistant = choice.get("message").ok_or("llama.cpp missing message")?;
        if assistant.get("role").and_then(Value::as_str) != Some("assistant") {
            return Err("llama.cpp response role must be assistant".into());
        }
        let content = match assistant.get("content") {
            None | Some(Value::Null) => "",
            Some(Value::String(s)) => s,
            _ => return Err("llama.cpp assistant content must be string".into()),
        };
        let raw_calls = match assistant.get("tool_calls") {
            None | Some(Value::Null) => &[][..],
            Some(Value::Array(array)) => array.as_slice(),
            _ => return Err("llama.cpp tool_calls must be array".into()),
        };
        if raw_calls.is_empty() {
            if content.trim().is_empty() {
                return Err("llama.cpp returned empty final answer".into());
            }
            self.wire.push(json!({"role":"assistant","content":content}));
            return Ok(ModelStep::Final(content.into()));
        }
        let mut calls = Vec::with_capacity(raw_calls.len());
        let mut normalized = Vec::with_capacity(raw_calls.len());
        let mut pending = Vec::with_capacity(raw_calls.len());
        for (idx, item) in raw_calls.iter().enumerate() {
            let function = item.get("function").ok_or("tool call missing function")?;
            let name = function.get("name").and_then(Value::as_str)
                .filter(|s| !s.trim().is_empty()).ok_or("tool call missing name")?;
            let raw_args = function.get("arguments").ok_or("tool call missing arguments")?;
            let arguments = match raw_args {
                Value::String(s) => serde_json::from_str::<Value>(s)
                    .map_err(|_| "tool call arguments contain invalid JSON")?,
                Value::Object(_) => raw_args.clone(),
                _ => return Err("tool call arguments must be JSON object".into()),
            };
            if !arguments.is_object() {
                return Err("tool call arguments must be JSON object".into());
            }
            let id = item.get("id").and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .unwrap_or_else(|| format!("call_{}_{}", self.turn_index, idx));
            if pending.iter().any(|(_, seen_id): &(String, String)| *seen_id == id) {
                return Err("duplicate tool call id".into());
            }
            pending.push((name.into(), id.clone()));
            calls.push(Call { name: name.into(), arguments: arguments.clone() });
            normalized.push(json!({
                "id":id, "type":"function",
                "function":{"name":name,"arguments":arguments.to_string()}
            }));
        }
        self.wire.push(json!({"role":"assistant","content":content,"tool_calls":normalized}));
        self.pending = pending;
        self.turn_index += 1;
        Ok(ModelStep::Calls(calls))
    }
}

impl Model for LlamaCpp {
    fn next<'a>(&'a mut self, history: &'a [Message], available: &'a [ToolSchema])
        -> BoxFuture<'a, Result<ModelStep, String>>
    {
        Box::pin(self.complete(history, available))
    }
}
