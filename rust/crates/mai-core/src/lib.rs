//! MAI minimal, testable Rust agent core. No HTTP, MCP, or verifier.
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::sync::{Arc, atomic::{AtomicBool, Ordering}};

pub type Arguments = BTreeMap<String, String>;
pub type Handler = fn(&Arguments) -> Result<String, String>;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Role { User, Assistant, Tool }
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Message { pub role: Role, pub text: String }
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ToolCall {
    Activate(Vec<String>),
    Invoke { name: String, args: Arguments },
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ModelAction { Final(String), Tools(Vec<ToolCall>) }

/// A fake implementation is used in tests; llama.cpp integration is later.
pub trait Model {
    fn next(&mut self, history: &[Message], available: &[String]) -> Result<ModelAction, String>;
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Event {
    Started, Round(usize), Tool { name: String, ok: bool, result: String },
    Final(String), Cancelled, Failed(String),
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RunError { EmptyInput, EmptyAnswer, EmptyToolBatch, Model(String), Cancelled }
impl fmt::Display for RunError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self:?}")
    }
}
impl std::error::Error for RunError {}

/// Cooperative cancellation: async HTTP/child process cancellation is future work.
#[derive(Clone, Default)]
pub struct Cancellation(Arc<AtomicBool>);
impl Cancellation {
    pub fn cancel(&self) { self.0.store(true, Ordering::SeqCst); }
    pub fn is_cancelled(&self) -> bool { self.0.load(Ordering::SeqCst) }
}
#[derive(Default)]
pub struct ToolRegistry { definitions: BTreeMap<String, Handler> }
impl ToolRegistry {
    pub fn new() -> Self { Self::default() }
    pub fn register(&mut self, name: &str, handler: Handler) -> Result<(), String> {
        if name.trim().is_empty() || name == "tool_manual" {
            return Err("invalid or reserved tool name".into());
        }
        if self.definitions.insert(name.into(), handler).is_some() {
            return Err(format!("duplicate tool: {name}"));
        }
        Ok(())
    }
    pub fn names(&self) -> Vec<String> { self.definitions.keys().cloned().collect() }
    fn contains(&self, name: &str) -> bool { self.definitions.contains_key(name) }
    fn execute(&self, name: &str, args: &Arguments) -> Result<String, String> {
        self.definitions.get(name).ok_or_else(|| format!("unknown tool: {name}"))?(args)
    }
}
#[derive(Debug)]
pub struct Outcome { pub answer: String, pub history: Vec<Message>, pub rounds: usize }

pub struct AgentCore<M: Model> { pub model: M, pub tools: ToolRegistry }
impl<M: Model> AgentCore<M> {
    pub fn new(model: M, tools: ToolRegistry) -> Self { Self { model, tools } }

    /// No arbitrary maximum model-round count. The caller owns run state.
    pub fn run(&mut self, input: &str, cancel: &Cancellation, mut emit: impl FnMut(Event))
        -> Result<Outcome, RunError>
    {
        if input.trim().is_empty() { return fail(RunError::EmptyInput, &mut emit); }
        let mut history = vec![Message { role: Role::User, text: input.into() }];
        let mut activated = BTreeSet::<String>::new();
        let mut rounds = 0;
        emit(Event::Started);
        loop {
            check_cancel(cancel, &mut emit)?;
            rounds += 1;
            emit(Event::Round(rounds));
            let mut available = vec!["tool_manual".to_owned()];
            available.extend(activated.iter().cloned());
            let action = match self.model.next(&history, &available) {
                Ok(action) => action,
                Err(reason) => return fail(RunError::Model(reason), &mut emit),
            };
            check_cancel(cancel, &mut emit)?;
            match action {
                ModelAction::Final(answer) => {
                    if answer.trim().is_empty() { return fail(RunError::EmptyAnswer, &mut emit); }
                    history.push(Message { role: Role::Assistant, text: answer.clone() });
                    emit(Event::Final(answer.clone()));
                    return Ok(Outcome { answer, history, rounds });
                }
                ModelAction::Tools(calls) => {
                    if calls.is_empty() { return fail(RunError::EmptyToolBatch, &mut emit); }
                    for call in calls {
                        check_cancel(cancel, &mut emit)?;
                        let (name, result) = match call {
                            ToolCall::Activate(names) => {
                                // No partial activation on an invalid batch.
                                let result = if names.is_empty() {
                                    Err("tool_manual requires tool names".into())
                                } else if let Some(unknown) = names.iter().find(|n| !self.tools.contains(n)) {
                                    Err(format!("unknown tool: {unknown}"))
                                } else {
                                    activated.extend(names);
                                    Ok("activated".to_string())
                                };
                                ("tool_manual".to_string(), result)
                            }
                            ToolCall::Invoke { name, args } => {
                                let result = if !activated.contains(&name) {
                                    Err(format!("not activated: {name}"))
                                } else { self.tools.execute(&name, &args) };
                                (name, result)
                            }
                        };
                        let (ok, value) = match result {
                            Ok(value) => (true, value),
                            Err(err) => (false, err),
                        };
                        history.push(Message {
                            role: Role::Tool,
                            text: format!("{name}: {}{value}", if ok { "" } else { "ERROR: " }),
                        });
                        emit(Event::Tool { name, ok, result: value });
                    }
                }
            }
        }
    }
}
fn fail<T>(error: RunError, emit: &mut impl FnMut(Event)) -> Result<T, RunError> {
    emit(Event::Failed(error.to_string()));
    Err(error)
}
fn check_cancel(cancel: &Cancellation, emit: &mut impl FnMut(Event)) -> Result<(), RunError> {
    if cancel.is_cancelled() { emit(Event::Cancelled); Err(RunError::Cancelled) } else { Ok(()) }
}
