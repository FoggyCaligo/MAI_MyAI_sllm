//! Native, async, JSON-based MAI agent engine.
//! This crate has no MCP/verifier dependency and does not modify the legacy UI.
//! It is an isolated foundation, not a production-ready replacement for MAI.

pub mod llamacpp;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    future::Future,
    pin::Pin,
    sync::Arc,
    time::Duration,
};
use tokio::{sync::mpsc, time::timeout};
use tokio_util::sync::CancellationToken;

pub type BoxFuture<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;
pub type ToolFn = Arc<dyn Fn(Value) -> BoxFuture<'static, Result<Value, String>> + Send + Sync>;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    User,
    Assistant,
    Tool,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Message {
    pub role: Role,
    pub content: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Call {
    pub name: String,
    pub arguments: Value,
}

#[derive(Clone, Debug, PartialEq)]
pub enum ModelStep {
    Final(String),
    Calls(Vec<Call>),
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ToolSchema {
    pub name: String,
    pub description: String,
    pub parameters: Value,
}

pub trait Model: Send {
    fn next<'a>(
        &'a mut self,
        history: &'a [Message],
        available: &'a [ToolSchema],
    ) -> BoxFuture<'a, Result<ModelStep, String>>;
}

pub struct Tool {
    pub schema: ToolSchema,
    pub invoke: ToolFn,
    pub timeout: Duration,
}

#[derive(Default)]
pub struct Registry {
    tools: BTreeMap<String, Tool>,
}

impl Registry {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn register(&mut self, tool: Tool) -> Result<(), String> {
        let name = tool.schema.name.clone();
        if name.trim().is_empty() || name == "tool_manual" {
            return Err("invalid or reserved tool name".into());
        }
        if tool.timeout.is_zero() || self.tools.contains_key(&name) {
            return Err(format!("invalid timeout or duplicate tool: {name}"));
        }
        validate_schema(&tool.schema.parameters)?;
        self.tools.insert(name, tool);
        Ok(())
    }

    pub fn names(&self) -> Vec<String> {
        self.tools.keys().cloned().collect()
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Event {
    Started,
    Round {
        number: usize,
        callable: Vec<String>,
    },
    Tool {
        name: String,
        ok: bool,
        result: Value,
    },
    Final {
        answer: String,
    },
    Failed {
        error: String,
    },
    Cancelled,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RunError {
    InvalidInput,
    InvalidModelResponse,
    Model(String),
    ModelTimeout,
    Cancelled,
}

#[derive(Debug)]
pub struct Outcome {
    pub answer: String,
    pub rounds: usize,
    pub messages: Vec<Message>,
}

pub struct Engine<M: Model> {
    model: M,
    registry: Registry,
    model_timeout: Duration,
}

impl<M: Model> Engine<M> {
    pub fn new(model: M, registry: Registry, model_timeout: Duration) -> Result<Self, String> {
        if model_timeout.is_zero() {
            return Err("model timeout must be positive".into());
        }
        Ok(Self {
            model,
            registry,
            model_timeout,
        })
    }

    pub async fn run(
        &mut self,
        prompt: &str,
        cancellation: &CancellationToken,
        events: &mpsc::Sender<Event>,
    ) -> Result<Outcome, RunError> {
        self.run_with_history(prompt, &[], cancellation, events)
            .await
    }

    /// Start one run using a bounded, authenticated snapshot of prior chat.
    /// Tool events are intentionally not persisted as conversation history.
    pub async fn run_with_history(
        &mut self,
        prompt: &str,
        prior: &[Message],
        cancellation: &CancellationToken,
        events: &mpsc::Sender<Event>,
    ) -> Result<Outcome, RunError> {
        if prompt.trim().is_empty()
            || prior
                .iter()
                .any(|m| m.role == Role::Tool || m.content.trim().is_empty())
        {
            return self.finish_error(RunError::InvalidInput, events).await;
        }
        let mut messages = prior.to_vec();
        messages.push(Message {
            role: Role::User,
            content: prompt.into(),
        });
        let mut enabled = BTreeSet::new();
        let mut rounds = 0;
        emit(events, Event::Started).await;
        loop {
            if cancellation.is_cancelled() {
                return self.finish_error(RunError::Cancelled, events).await;
            }
            rounds += 1;
            let callable_this_round = enabled.clone();
            let mut schemas = vec![manual_schema(&self.registry)];
            schemas.extend(
                enabled
                    .iter()
                    .filter_map(|name| self.registry.tools.get(name).map(|t| t.schema.clone())),
            );
            emit(
                events,
                Event::Round {
                    number: rounds,
                    callable: schemas.iter().map(|t| t.name.clone()).collect(),
                },
            )
            .await;

            let step = tokio::select! {
                biased;
                _ = cancellation.cancelled() => Err(RunError::Cancelled),
                result = timeout(self.model_timeout, self.model.next(&messages, &schemas)) => {
                    match result {
                        Ok(Ok(step)) => Ok(step),
                        Ok(Err(error)) => Err(RunError::Model(error)),
                        Err(_) => Err(RunError::ModelTimeout),
                    }
                }
            };
            let step = match step {
                Ok(step) => step,
                Err(e) => return self.finish_error(e, events).await,
            };
            if cancellation.is_cancelled() {
                return self.finish_error(RunError::Cancelled, events).await;
            }
            match step {
                ModelStep::Final(answer) => {
                    if answer.trim().is_empty() {
                        return self
                            .finish_error(RunError::InvalidModelResponse, events)
                            .await;
                    }
                    messages.push(Message {
                        role: Role::Assistant,
                        content: answer.clone(),
                    });
                    emit(
                        events,
                        Event::Final {
                            answer: answer.clone(),
                        },
                    )
                    .await;
                    return Ok(Outcome {
                        answer,
                        rounds,
                        messages,
                    });
                }
                ModelStep::Calls(calls) => {
                    if calls.is_empty() {
                        return self
                            .finish_error(RunError::InvalidModelResponse, events)
                            .await;
                    }
                    for call in calls {
                        if cancellation.is_cancelled() {
                            return self.finish_error(RunError::Cancelled, events).await;
                        }
                        let name = call.name;
                        let result = if name == "tool_manual" {
                            activate(&self.registry, &mut enabled, &call.arguments)
                        } else if !callable_this_round.contains(&name) {
                            Err(format!("tool not activated: {name}"))
                        } else if let Some(tool) = self.registry.tools.get(&name) {
                            match validate_arguments(&tool.schema.parameters, &call.arguments) {
                                Err(e) => Err(e),
                                Ok(()) => {
                                    tokio::select! {
                                        biased;
                                        _ = cancellation.cancelled() => {
                                            return self.finish_error(RunError::Cancelled, events).await;
                                        }
                                        res = timeout(tool.timeout, (tool.invoke)(call.arguments)) => {
                                            match res {
                                                Ok(result) => result,
                                                Err(_) => Err(format!("tool timed out: {name}")),
                                            }
                                        }
                                    }
                                }
                            }
                        } else {
                            Err(format!("unknown tool: {name}"))
                        };
                        let (ok, value) = match result {
                            Ok(value) => (true, value),
                            Err(reason) => (false, json!({"error":reason})),
                        };
                        messages.push(Message {
                            role: Role::Tool,
                            content: json!({"name":name,"ok":ok,"result":value}).to_string(),
                        });
                        emit(
                            events,
                            Event::Tool {
                                name,
                                ok,
                                result: value,
                            },
                        )
                        .await;
                    }
                }
            }
        }
    }

    async fn finish_error<T>(
        &self,
        error: RunError,
        events: &mpsc::Sender<Event>,
    ) -> Result<T, RunError> {
        match &error {
            RunError::Cancelled => emit(events, Event::Cancelled).await,
            other => {
                emit(
                    events,
                    Event::Failed {
                        error: format!("{other:?}"),
                    },
                )
                .await
            }
        }
        Err(error)
    }
}

async fn emit(channel: &mpsc::Sender<Event>, event: Event) {
    // Closed listeners must not turn a successful run into a false error.
    // Bounded channel provides backpressure while a listener is active.
    let _ = channel.send(event).await;
}

fn manual_schema(registry: &Registry) -> ToolSchema {
    ToolSchema {
        name: "tool_manual".into(),
        description: "Activate tools by their exact registered names".into(),
        parameters: json!({
            "type":"object",
            "properties":{"tools":{"type":"array","items":{"type":"string","enum":registry.names()}}},
            "required":["tools"], "additionalProperties":false
        }),
    }
}

fn activate(
    registry: &Registry,
    enabled: &mut BTreeSet<String>,
    args: &Value,
) -> Result<Value, String> {
    let schema = manual_schema(registry);
    validate_arguments(&schema.parameters, args)?;
    let selected = args
        .get("tools")
        .and_then(Value::as_array)
        .ok_or("tools must be an array")?;
    if selected.is_empty() {
        return Err("at least one tool is required".into());
    }
    let names: Vec<&str> = selected.iter().map(|v| v.as_str().unwrap_or("")).collect();
    if let Some(name) = names
        .iter()
        .find(|name| !registry.tools.contains_key(**name))
    {
        return Err(format!("unknown tool: {name}"));
    }
    enabled.extend(names.into_iter().map(str::to_string));
    Ok(json!({"activated":true}))
}

/// Strict object-schema subset: object, properties, required,
/// additionalProperties=false, enum, array items, and primitive types.
/// Full JSON Schema support is intentionally *not* claimed here.
fn validate_schema(schema: &Value) -> Result<(), String> {
    if schema.get("type") != Some(&json!("object")) {
        return Err("tool schema root must have type=object".into());
    }
    if !schema.get("properties").is_some_and(Value::is_object) {
        return Err("tool schema properties must be an object".into());
    }
    Ok(())
}

pub fn validate_arguments(schema: &Value, args: &Value) -> Result<(), String> {
    validate_schema(schema)?;
    let obj = args
        .as_object()
        .ok_or("tool arguments must be a JSON object")?;
    let props = schema
        .get("properties")
        .and_then(Value::as_object)
        .ok_or("properties missing")?;
    if let Some(required) = schema.get("required").and_then(Value::as_array) {
        for field in required {
            let name = field.as_str().ok_or("invalid required entry")?;
            if !obj.contains_key(name) {
                return Err(format!("missing field: {name}"));
            }
        }
    }
    for (name, value) in obj {
        let Some(rule) = props.get(name) else {
            if schema.get("additionalProperties") == Some(&Value::Bool(false)) {
                return Err(format!("unknown field: {name}"));
            }
            continue;
        };
        validate_value(rule, value, name)?;
    }
    Ok(())
}

fn validate_value(rule: &Value, value: &Value, field: &str) -> Result<(), String> {
    let valid = match rule.get("type").and_then(Value::as_str) {
        Some("string") => value.is_string(),
        Some("integer") => value.is_i64() || value.is_u64(),
        Some("number") => value.is_number(),
        Some("boolean") => value.is_boolean(),
        Some("array") => value.is_array(),
        Some("object") => value.is_object(),
        Some("null") => value.is_null(),
        Some(_) => return Err(format!("unsupported schema type for {field}")),
        None => return Err(format!("missing schema type for {field}")),
    };
    if !valid {
        return Err(format!("invalid type for {field}"));
    }
    if let Some(options) = rule.get("enum").and_then(Value::as_array) {
        if !options.contains(value) {
            return Err(format!("invalid enum for {field}"));
        }
    }
    if let Some(items) = value.as_array() {
        if let Some(item_rule) = rule.get("items") {
            for (index, item) in items.iter().enumerate() {
                validate_value(item_rule, item, &format!("{field}[{index}]"))?;
            }
        }
    }
    if let Some(nested) = value.as_object() {
        if let Some(properties) = rule.get("properties").and_then(Value::as_object) {
            validate_nested(rule, nested, properties, field)?;
        }
    }
    Ok(())
}

fn validate_nested(
    rule: &Value,
    obj: &Map<String, Value>,
    properties: &Map<String, Value>,
    path: &str,
) -> Result<(), String> {
    if let Some(required) = rule.get("required").and_then(Value::as_array) {
        for entry in required {
            let name = entry.as_str().ok_or("invalid nested required entry")?;
            if !obj.contains_key(name) {
                return Err(format!("missing field: {path}.{name}"));
            }
        }
    }
    for (key, value) in obj {
        if let Some(subrule) = properties.get(key) {
            validate_value(subrule, value, &format!("{path}.{key}"))?;
        } else if rule.get("additionalProperties") == Some(&Value::Bool(false)) {
            return Err(format!("unknown field: {path}.{key}"));
        }
    }
    Ok(())
}
