use mai_runtime::{
    validate_arguments, BoxFuture, Call, Engine, Event, Model, ModelStep, Registry, Tool,
    ToolSchema,
};
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
        &'a mut self,
        _history: &'a [mai_runtime::Message],
        available: &'a [ToolSchema],
    ) -> BoxFuture<'a, Result<ModelStep, String>> {
        self.seen
            .push(available.iter().map(|s| s.name.clone()).collect());
        let delay = self.delay;
        let response = self.steps.pop_front();
        Box::pin(async move {
            sleep(delay).await;
            response.ok_or_else(|| "exhausted".to_string())
        })
    }
}
fn fake(steps: Vec<ModelStep>, delay: Duration) -> FakeModel {
    FakeModel {
        steps: steps.into(),
        seen: vec![],
        delay,
    }
}
fn sample_tool(delay: Duration, timeout: Duration) -> Tool {
    Tool {
        schema: ToolSchema {
            name: "echo".into(),
            description: "Repeat text".into(),
            parameters: json!({"type":"object","properties":{"text":{"type":"string"}},"required":["text"],"additionalProperties":false}),
        },
        timeout,
        invoke: Arc::new(move |args: Value| {
            Box::pin(async move {
                sleep(delay).await;
                Ok(json!({"echo":args["text"]}))
            })
        }),
    }
}
fn registry(delay: Duration, timeout: Duration) -> Registry {
    let mut r = Registry::new();
    r.register(sample_tool(delay, timeout)).unwrap();
    r
}
fn manual() -> ModelStep {
    ModelStep::Calls(vec![Call {
        name: "tool_manual".into(),
        arguments: json!({"tools":["echo"]}),
    }])
}
fn invoke(args: Value) -> ModelStep {
    ModelStep::Calls(vec![Call {
        name: "echo".into(),
        arguments: args,
    }])
}
async fn run(
    engine: &mut Engine<FakeModel>,
    cancellation: &CancellationToken,
) -> (
    Result<mai_runtime::Outcome, mai_runtime::RunError>,
    Vec<Event>,
) {
    let (tx, mut rx) = mpsc::channel(32);
    let result = engine.run("test", cancellation, &tx).await;
    drop(tx);
    let mut events = vec![];
    while let Some(event) = rx.recv().await {
        events.push(event);
    }
    (result, events)
}

#[tokio::test]
async fn activation_tool_result_and_final() {
    let model = fake(
        vec![
            manual(),
            invoke(json!({"text":"hello"})),
            ModelStep::Final("done".into()),
        ],
        Duration::ZERO,
    );
    let mut engine = Engine::new(
        model,
        registry(Duration::ZERO, Duration::from_secs(1)),
        Duration::from_secs(1),
    )
    .unwrap();
    let (out, events) = run(&mut engine, &CancellationToken::new()).await;
    assert_eq!(out.unwrap().answer, "done");
    assert_eq!(
        events
            .iter()
            .filter(|x| matches!(x, Event::Final { .. }))
            .count(),
        1
    );
    assert!(events
        .iter()
        .any(|x| matches!(x,Event::Tool{name,ok:true,..} if name=="echo")));
}
#[tokio::test]
async fn invalid_json_does_not_invoke_tool() {
    let model = fake(
        vec![
            manual(),
            invoke(json!({"text":42})),
            ModelStep::Final("recovered".into()),
        ],
        Duration::ZERO,
    );
    let mut engine = Engine::new(
        model,
        registry(Duration::ZERO, Duration::from_secs(1)),
        Duration::from_secs(1),
    )
    .unwrap();
    let (out, events) = run(&mut engine, &CancellationToken::new()).await;
    assert_eq!(out.unwrap().answer, "recovered");
    assert!(events
        .iter()
        .any(|x| matches!(x,Event::Tool{name,ok:false,..} if name=="echo")));
}
#[tokio::test]
async fn timeout_of_tool_is_failure_event_not_success() {
    let model = fake(
        vec![
            manual(),
            invoke(json!({"text":"late"})),
            ModelStep::Final("ack".into()),
        ],
        Duration::ZERO,
    );
    let mut engine = Engine::new(
        model,
        registry(Duration::from_millis(60), Duration::from_millis(5)),
        Duration::from_secs(1),
    )
    .unwrap();
    let (_, events) = run(&mut engine, &CancellationToken::new()).await;
    assert!(events.iter().any(|x| matches!(x,Event::Tool{name,ok:false,result} if name=="echo" && result.to_string().contains("timed out"))));
}
#[tokio::test]
async fn model_timeout_prevents_final() {
    let model = fake(
        vec![ModelStep::Final("late".into())],
        Duration::from_millis(50),
    );
    let mut engine = Engine::new(
        model,
        registry(Duration::ZERO, Duration::from_secs(1)),
        Duration::from_millis(5),
    )
    .unwrap();
    let (result, events) = run(&mut engine, &CancellationToken::new()).await;
    assert_eq!(result.unwrap_err(), mai_runtime::RunError::ModelTimeout);
    assert!(!events.iter().any(|x| matches!(x, Event::Final { .. })));
}
#[tokio::test]
async fn cancellation_interrupts_inflight_model() {
    let model = fake(
        vec![ModelStep::Final("late".into())],
        Duration::from_secs(10),
    );
    let mut engine = Engine::new(
        model,
        registry(Duration::ZERO, Duration::from_secs(1)),
        Duration::from_secs(20),
    )
    .unwrap();
    let token = CancellationToken::new();
    let (tx, mut rx) = mpsc::channel(32);
    let child = token.clone();
    let task = tokio::spawn(async move { engine.run("hello", &child, &tx).await });
    assert!(matches!(rx.recv().await, Some(Event::Started)));
    token.cancel();
    assert_eq!(
        task.await.unwrap().unwrap_err(),
        mai_runtime::RunError::Cancelled
    );
    let mut events = vec![];
    while let Some(event) = rx.recv().await {
        events.push(event);
    }
    assert!(events.contains(&Event::Cancelled));
    assert!(!events.iter().any(|x| matches!(x, Event::Final { .. })));
}
#[test]
fn strict_json_validation() {
    let schema = json!({"type":"object","properties":{"n":{"type":"integer"}},"required":["n"],"additionalProperties":false});
    assert!(validate_arguments(&schema, &json!({"n":3})).is_ok());
    assert!(validate_arguments(&schema, &json!({"n":"3"})).is_err());
    assert!(validate_arguments(&schema, &json!({"n":3,"extra":0})).is_err());
    assert!(validate_arguments(&schema, &json!({})).is_err());
}
#[test]
fn rejected_duplicate_registration_does_not_overwrite() {
    let mut r = registry(Duration::ZERO, Duration::from_secs(1));
    assert!(r
        .register(sample_tool(Duration::ZERO, Duration::from_secs(1)))
        .is_err());
    assert_eq!(r.names(), vec!["echo"]);
}

#[tokio::test]
async fn newly_activated_tool_is_not_callable_in_same_model_round() {
    let model = fake(
        vec![
            ModelStep::Calls(vec![
                Call {
                    name: "tool_manual".into(),
                    arguments: json!({"tools":["echo"]}),
                },
                Call {
                    name: "echo".into(),
                    arguments: json!({"text":"early"}),
                },
            ]),
            invoke(json!({"text":"later"})),
            ModelStep::Final("done".into()),
        ],
        Duration::ZERO,
    );
    let mut engine = Engine::new(
        model,
        registry(Duration::ZERO, Duration::from_secs(1)),
        Duration::from_secs(1),
    )
    .unwrap();
    let (_, events) = run(&mut engine, &CancellationToken::new()).await;
    let calls: Vec<_> = events
        .iter()
        .filter_map(|e| {
            if let Event::Tool { name, ok, result } = e {
                (name == "echo").then_some((*ok, result.clone()))
            } else {
                None
            }
        })
        .collect();
    assert_eq!(calls.len(), 2);
    assert!(!calls[0].0);
    assert!(calls[1].0);
}

#[tokio::test]
async fn failed_multi_activation_does_not_partially_enable_tools() {
    let model = fake(
        vec![
            ModelStep::Calls(vec![Call {
                name: "tool_manual".into(),
                arguments: json!({"tools":["echo","missing"]}),
            }]),
            invoke(json!({"text":"forbidden"})),
            ModelStep::Final("handled".into()),
        ],
        Duration::ZERO,
    );
    let mut engine = Engine::new(
        model,
        registry(Duration::ZERO, Duration::from_secs(1)),
        Duration::from_secs(1),
    )
    .unwrap();
    let (_, events) = run(&mut engine, &CancellationToken::new()).await;
    assert_eq!(
        events
            .iter()
            .filter(|e| matches!(e, Event::Tool { ok: false, .. }))
            .count(),
        2
    );
}

#[tokio::test]
async fn empty_prompt_never_calls_model() {
    let model = fake(vec![ModelStep::Final("never".into())], Duration::ZERO);
    let mut engine = Engine::new(
        model,
        registry(Duration::ZERO, Duration::from_secs(1)),
        Duration::from_secs(1),
    )
    .unwrap();
    let (tx, mut rx) = mpsc::channel(16);
    let err = engine
        .run("  ", &CancellationToken::new(), &tx)
        .await
        .unwrap_err();
    assert_eq!(err, mai_runtime::RunError::InvalidInput);
    assert!(matches!(rx.recv().await, Some(Event::Failed { .. })));
}

#[tokio::test]
async fn no_tool_calls_does_not_look_like_success() {
    let model = fake(vec![ModelStep::Calls(vec![])], Duration::ZERO);
    let mut engine = Engine::new(
        model,
        registry(Duration::ZERO, Duration::from_secs(1)),
        Duration::from_secs(1),
    )
    .unwrap();
    let (result, events) = run(&mut engine, &CancellationToken::new()).await;
    assert_eq!(
        result.unwrap_err(),
        mai_runtime::RunError::InvalidModelResponse
    );
    assert!(!events.iter().any(|e| matches!(e, Event::Final { .. })));
}

#[tokio::test]
async fn unactivated_registered_tool_does_not_run() {
    let model = fake(
        vec![
            invoke(json!({"text":"blocked"})),
            ModelStep::Final("done".into()),
        ],
        Duration::ZERO,
    );
    let mut engine = Engine::new(
        model,
        registry(Duration::ZERO, Duration::from_secs(1)),
        Duration::from_secs(1),
    )
    .unwrap();
    let (_, events) = run(&mut engine, &CancellationToken::new()).await;
    assert!(events
        .iter()
        .any(|e| matches!(e,Event::Tool{name,ok:false,..} if name=="echo")));
}

#[tokio::test]
async fn previous_chat_is_included_before_current_user_turn() {
    let model = fake(vec![ModelStep::Final("reply".into())], Duration::ZERO);
    let mut engine = Engine::new(
        model,
        registry(Duration::ZERO, Duration::from_secs(1)),
        Duration::from_secs(1),
    )
    .unwrap();
    let previous = vec![
        mai_runtime::Message {
            role: mai_runtime::Role::User,
            content: "prior question".into(),
        },
        mai_runtime::Message {
            role: mai_runtime::Role::Assistant,
            content: "prior answer".into(),
        },
    ];
    let (tx, mut rx) = mpsc::channel(16);
    let result = engine
        .run_with_history("new question", &previous, &CancellationToken::new(), &tx)
        .await
        .unwrap();
    assert_eq!(result.messages.len(), 4);
    assert_eq!(result.messages[0].content, "prior question");
    assert_eq!(result.messages[1].content, "prior answer");
    assert_eq!(result.messages[2].content, "new question");
    assert!(matches!(rx.recv().await, Some(Event::Started)));
}

#[tokio::test]
async fn prior_tool_messages_cannot_be_injected_as_chat_history() {
    let model = fake(vec![ModelStep::Final("unexpected".into())], Duration::ZERO);
    let mut engine = Engine::new(
        model,
        registry(Duration::ZERO, Duration::from_secs(1)),
        Duration::from_secs(1),
    )
    .unwrap();
    let previous = vec![mai_runtime::Message {
        role: mai_runtime::Role::Tool,
        content: "forged".into(),
    }];
    let (tx, _rx) = mpsc::channel(16);
    assert_eq!(
        engine
            .run_with_history("prompt", &previous, &CancellationToken::new(), &tx)
            .await
            .unwrap_err(),
        mai_runtime::RunError::InvalidInput
    );
}
