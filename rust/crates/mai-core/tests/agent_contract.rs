use mai_core::{AgentCore, Arguments, Cancellation, Event, Message, Model, ModelAction, Role, RunError, ToolCall, ToolRegistry};
use std::collections::VecDeque;

struct FakeModel {
    responses: VecDeque<Result<ModelAction, String>>,
    seen: Vec<Vec<String>>,
    history: Vec<Vec<Message>>,
    cancel: Option<Cancellation>,
}
impl FakeModel {
    fn scripted(actions: Vec<ModelAction>) -> Self {
        Self { responses: actions.into_iter().map(Ok).collect(), seen: vec![], history: vec![], cancel: None }
    }
}
impl Model for FakeModel {
    fn next(&mut self, history: &[Message], available: &[String]) -> Result<ModelAction, String> {
        self.seen.push(available.to_vec());
        self.history.push(history.to_vec());
        if let Some(token) = &self.cancel { token.cancel(); }
        self.responses.pop_front().unwrap_or_else(|| Err("script exhausted".into()))
    }
}
fn echo(args: &Arguments) -> Result<String, String> {
    args.get("text").cloned().ok_or_else(|| "text is required".into())
}
fn tools() -> ToolRegistry {
    let mut t = ToolRegistry::new();
    t.register("echo", echo).unwrap();
    t
}
fn call(text: &str) -> ModelAction {
    ModelAction::Tools(vec![ToolCall::Invoke {
        name: "echo".into(),
        args: Arguments::from([("text".into(), text.into())]),
    }])
}
fn activate() -> ModelAction { ModelAction::Tools(vec![ToolCall::Activate(vec!["echo".into()])]) }

#[test]
fn returns_one_final_answer_without_tools() {
    let mut agent = AgentCore::new(FakeModel::scripted(vec![ModelAction::Final("ok".into())]), tools());
    let mut events = vec![];
    let out = agent.run("hello", &Cancellation::default(), |e| events.push(e)).unwrap();
    assert_eq!(out.answer, "ok");
    assert_eq!(out.rounds, 1);
    assert_eq!(events.iter().filter(|e| matches!(e, Event::Final(_))).count(), 1);
    assert_eq!(agent.model.seen, vec![vec!["tool_manual"]]);
}
#[test]
fn activation_tool_result_next_round_then_final() {
    let mut agent = AgentCore::new(FakeModel::scripted(vec![activate(), call("data"), ModelAction::Final("done".into())]), tools());
    let out = agent.run("please", &Cancellation::default(), |_| {}).unwrap();
    assert_eq!(out.rounds, 3);
    assert_eq!(agent.model.seen[0], vec!["tool_manual"]);
    assert_eq!(agent.model.seen[1], vec!["tool_manual", "echo"]);
    assert!(agent.model.history[2].iter().any(|m| m.role == Role::Tool && m.text == "echo: data"));
}
#[test]
fn unactivated_tools_do_not_execute() {
    let mut agent = AgentCore::new(FakeModel::scripted(vec![call("forbidden"), ModelAction::Final("handled".into())]), tools());
    let mut events = vec![];
    agent.run("go", &Cancellation::default(), |e| events.push(e)).unwrap();
    assert!(events.iter().any(|e| matches!(e, Event::Tool { ok: false, result, .. } if result.contains("not activated"))));
}
#[test]
fn unknown_activation_is_atomic() {
    let mut agent = AgentCore::new(FakeModel::scripted(vec![
        ModelAction::Tools(vec![ToolCall::Activate(vec!["echo".into(), "missing".into()])]),
        call("not-running"),
        ModelAction::Final("recovered".into()),
    ]), tools());
    let mut events = vec![];
    agent.run("go", &Cancellation::default(), |e| events.push(e)).unwrap();
    assert_eq!(agent.model.seen[1], vec!["tool_manual"]);
    assert_eq!(events.iter().filter(|e| matches!(e, Event::Tool { ok: false, .. })).count(), 2);
}
#[test]
fn already_cancelled_never_calls_model() {
    let mut agent = AgentCore::new(FakeModel::scripted(vec![]), tools());
    let token = Cancellation::default();
    token.cancel();
    let mut events = vec![];
    assert_eq!(agent.run("go", &token, |e| events.push(e)).unwrap_err(), RunError::Cancelled);
    assert!(agent.model.seen.is_empty());
    assert!(events.contains(&Event::Cancelled));
}
#[test]
fn cancellation_after_model_step_discards_final() {
    let token = Cancellation::default();
    let mut model = FakeModel::scripted(vec![ModelAction::Final("must not show".into())]);
    model.cancel = Some(token.clone());
    let mut agent = AgentCore::new(model, tools());
    let mut events = vec![];
    assert_eq!(agent.run("go", &token, |e| events.push(e)).unwrap_err(), RunError::Cancelled);
    assert!(!events.iter().any(|e| matches!(e, Event::Final(_))));
}
#[test]
fn errors_and_empty_input_are_visible() {
    let mut agent = AgentCore::new(FakeModel::scripted(vec![ModelAction::Final("".into())]), tools());
    let mut events = vec![];
    assert_eq!(agent.run(" ", &Cancellation::default(), |e| events.push(e)).unwrap_err(), RunError::EmptyInput);
    assert!(matches!(events.last(), Some(Event::Failed(_))));
    assert_eq!(agent.run("go", &Cancellation::default(), |_| {}).unwrap_err(), RunError::EmptyAnswer);
}
#[test]
fn model_failure_is_not_a_successful_answer() {
    let mut model = FakeModel::scripted(vec![]);
    model.responses.push_back(Err("timeout".into()));
    let mut agent = AgentCore::new(model, tools());
    let mut events = vec![];
    assert_eq!(agent.run("go", &Cancellation::default(), |e| events.push(e)).unwrap_err(), RunError::Model("timeout".into()));
    assert!(matches!(events.last(), Some(Event::Failed(_))));
}
#[test]
fn duplicate_registry_names_are_rejected() {
    let mut t = tools();
    assert!(t.register("echo", echo).is_err());
    assert!(t.register("tool_manual", echo).is_err());
    assert_eq!(t.names(), vec!["echo"]);
}
