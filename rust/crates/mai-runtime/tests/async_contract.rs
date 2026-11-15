use mai_runtime::{Call, Engine, Event, Model, ModelStep, Registry, Tool, ToolSchema, BoxFuture, validate_arguments};
use serde_json::{json, Value};
use std::{collections::VecDeque, sync::Arc, time::Duration};
use tokio::{sync::mpsc, time::sleep};
use tokio_util::sync::CancellationToken;

struct FakeModel {
    steps: VecDeque<ModelStep>,
    seen: Vec<Vec<String>>,
    delay: Duration,
}
impl Model for FakeModel {
    fn next<'a>(
        &'a mut self, _history: &'a [mai_runtime::Message],
        available: &'a [ToolSchema],
    ) -> BoxFuture<'a, Result<ModelStep, String>> {
        self.seen.push(available.iter().map(|s| s.name.clone()).collect());
        let delay = self.delay;
        let response = self.steps.pop_front();
        Box::pin(async move {
            sleep(delay).await;
            response.ok_or_else(|| "exhausted".to_string())
        })
    }
}
fn fake(steps: Vec<ModelStep>, delay: Duration) -> FakeModel {
    FakeModel { steps: steps.into(), seen: vec![], delay }
}
fn sample_tool(delay: Duration, timeout: Duration) -> Tool {
    Tool {
        schema: ToolSchema {
            name: "echo".into(), description: "Repeat text".into(),
            parameters: json!({"type":"object","properties":{"text":{"type":"string"}},"required":["text"],"additionalProperties":false}),
        },
        timeout,
        invoke: Arc::new(move |args: Value| Box::pin(async move {
            sleep(delay).await;
            Ok(json!({"echo":args["text"]}))
        })),
    }
}
fn registry(delay: Duration, timeout: Duration) -> Registry {
    let mut r = Registry::new();
    r.register(sample_tool(delay, timeout)).unwrap();
    r
}
fn manual() -> ModelStep {
    ModelStep::Calls(vec![Call { name:"tool_manual".into(),arguments:json!({"tools":["echo"]}) }])
}
fn invoke(args: Value) -> ModelStep {
    ModelStep::Calls(vec![Call { name:"echo".into(), arguments:args }])
}
async fn run(
    engine: &mut Engine<FakeModel>, cancellation: &CancellationToken,
) -> (Result<mai_runtime::Outcome, mai_runtime::RunError>,Vec<Event>) {
    let (tx, mut rx) = mpsc::channel(32);
    let result = engine.run("test", cancellation, &tx).await;
    drop(tx);
    let mut events = vec![];
    while let Some(event) = rx.recv().await { events.push(event); }
    (result, events)
}

#[tokio::test]
async fn activation_tool_result_and_final() {
    let model = fake(vec![
        manual(), invoke(json!({"text":"hello"})), ModelStep::Final("done".into()),
    ], Duration::ZERO);
    let mut engine = Engine::new(model, registry(Duration::ZERO,Duration::from_secs(1)),Duration::from_secs(1)).unwrap();
    let (out, events) = run(&mut engine,&CancellationToken::new()).await;
    assert_eq!(out.unwrap().answer,"done");
    assert_eq!(events.iter().filter(|x| matches!(x,Event::Final{..})).count(),1);
    assert!(events.iter().any(|x| matches!(x,Event::Tool{name,ok:true,..} if name=="echo")));
}
#[tokio::test]
async fn invalid_json_does_not_invoke_tool() {
    let model=fake(vec![manual(),invoke(json!({"text":42})),ModelStep::Final("recovered".into())],Duration::ZERO);
    let mut engine=Engine::new(model,registry(Duration::ZERO,Duration::from_secs(1)),Duration::from_secs(1)).unwrap();
    let (out,events)=run(&mut engine,&CancellationToken::new()).await;
    assert_eq!(out.unwrap().answer,"recovered");
    assert!(events.iter().any(|x| matches!(x,Event::Tool{name,ok:false,..} if name=="echo")));
}
#[tokio::test]
async fn timeout_of_tool_is_failure_event_not_success() {
    let model=fake(vec![manual(),invoke(json!({"text":"late"})),ModelStep::Final("ack".into())],Duration::ZERO);
    let mut engine=Engine::new(model,registry(Duration::from_millis(60),Duration::from_millis(5)),Duration::from_secs(1)).unwrap();
    let (_,events)=run(&mut engine,&CancellationToken::new()).await;
    assert!(events.iter().any(|x| matches!(x,Event::Tool{name,ok:false,result} if name=="echo" && result.to_string().contains("timed out"))));
}
#[tokio::test]
async fn model_timeout_prevents_final() {
    let model=fake(vec![ModelStep::Final("late".into())],Duration::from_millis(50));
    let mut engine=Engine::new(model,registry(Duration::ZERO,Duration::from_secs(1)),Duration::from_millis(5)).unwrap();
    let (result,events)=run(&mut engine,&CancellationToken::new()).await;
    assert_eq!(result.unwrap_err(),mai_runtime::RunError::ModelTimeout);
    assert!(!events.iter().any(|x| matches!(x,Event::Final{..})));
}
#[tokio::test]
async fn cancellation_interrupts_inflight_model() {
    let model=fake(vec![ModelStep::Final("late".into())],Duration::from_secs(10));
    let mut engine=Engine::new(model,registry(Duration::ZERO,Duration::from_secs(1)),Duration::from_secs(20)).unwrap();
    let token=CancellationToken::new();
    let (tx,mut rx)=mpsc::channel(32);
    let child=token.clone();
    let task=tokio::spawn(async move { engine.run("hello",&child,&tx).await });
    assert!(matches!(rx.recv().await,Some(Event::Started)));
    token.cancel();
    assert_eq!(task.await.unwrap().unwrap_err(),mai_runtime::RunError::Cancelled);
    let mut events=vec![];
    while let Some(event)=rx.recv().await { events.push(event); }
    assert!(events.contains(&Event::Cancelled));
    assert!(!events.iter().any(|x| matches!(x,Event::Final{..})));
}
#[test]
fn strict_json_validation() {
    let schema=json!({"type":"object","properties":{"n":{"type":"integer"}},"required":["n"],"additionalProperties":false});
    assert!(validate_arguments(&schema,&json!({"n":3})).is_ok());
    assert!(validate_arguments(&schema,&json!({"n":"3"})).is_err());
    assert!(validate_arguments(&schema,&json!({"n":3,"extra":0})).is_err());
    assert!(validate_arguments(&schema,&json!({})).is_err());
}
#[test]
fn rejected_duplicate_registration_does_not_overwrite() {
    let mut r=registry(Duration::ZERO,Duration::from_secs(1));
    assert!(r.register(sample_tool(Duration::ZERO,Duration::from_secs(1))).is_err());
    assert_eq!(r.names(),vec!["echo"]);
}
