use mai_runtime::{llamacpp::{LlamaConfig,LlamaCpp}, Message,Model,ModelStep,Role,ToolSchema};
use serde_json::{json, Value};
use tokio::{io::{AsyncReadExt,AsyncWriteExt},net::TcpListener};

async fn serve(responses: Vec<Value>) -> (String, tokio::task::JoinHandle<Vec<Value>>) {
    let listener=TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url=format!("http://{}",listener.local_addr().unwrap());
    let task=tokio::spawn(async move {
        let mut captured=vec![];
        for response in responses {
            let (mut socket,_)=listener.accept().await.unwrap();
            let mut bytes=Vec::new();
            let mut buffer=[0u8;4096];
            loop {
                let read=socket.read(&mut buffer).await.unwrap();
                if read==0 { break; }
                bytes.extend_from_slice(&buffer[..read]);
                let Some(split)=bytes.windows(4).position(|x| x==b"\r\n\r\n") else { continue };
                let headers=String::from_utf8_lossy(&bytes[..split]);
                let length=headers.lines().find_map(|line|{
                    let (key,value)=line.split_once(':')?;
                    (key.trim().eq_ignore_ascii_case("content-length")).then(||value.trim().parse::<usize>().ok()).flatten()
                }).unwrap_or(0);
                if bytes.len()>=split+4+length { break; }
            }
            let split=bytes.windows(4).position(|x|x==b"\r\n\r\n").unwrap();
            captured.push(serde_json::from_slice::<Value>(&bytes[split+4..]).unwrap());
            let body=response.to_string();
            let response=format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",body.len(),body);
            socket.write_all(response.as_bytes()).await.unwrap();
        }
        captured
    });
    (url,task)
}
fn config(endpoint:String)->LlamaConfig {
    LlamaConfig{endpoint,model:"gguf-test".into(),max_output_tokens:128}
}
fn user()->Vec<Message>{vec![Message{role:Role::User,content:"hello".into()}]}
fn manual()->Vec<ToolSchema>{vec![ToolSchema{name:"tool_manual".into(),description:"Activate tools".into(),parameters:json!({"type":"object","properties":{"tools":{"type":"array","items":{"type":"string"}}}})}]}
#[tokio::test]
async fn sends_correct_chat_payload_and_returns_final() {
    let (host,task)=serve(vec![json!({"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"안녕"}}]})]).await;
    let mut model=LlamaCpp::new(config(host)).unwrap();
    assert_eq!(model.next(&user(),&manual()).await.unwrap(),ModelStep::Final("안녕".into()));
    let sent=task.await.unwrap();
    assert_eq!(sent[0]["model"],"gguf-test");
    assert_eq!(sent[0]["tools"][0]["function"]["name"],"tool_manual");
    assert_eq!(sent[0]["cache_prompt"],true);
}
#[tokio::test]
async fn maps_assistant_calls_and_correlated_tool_results() {
    let (host,task)=serve(vec![
        json!({"choices":[{"finish_reason":"tool_calls","message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-x","type":"function","function":{"name":"tool_manual","arguments":"{\"tools\":[\"echo\"]}"}}]}}]}),
        json!({"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"done"}}]}),
    ]).await;
    let mut model=LlamaCpp::new(config(host)).unwrap();
    let history=user();
    let step=model.next(&history,&manual()).await.unwrap();
    assert!(matches!(step,ModelStep::Calls(_)));
    let mut history=history;
    history.push(Message{role:Role::Tool,content:json!({"name":"tool_manual","ok":true,"result":{"activated":true}}).to_string()});
    assert_eq!(model.next(&history,&manual()).await.unwrap(),ModelStep::Final("done".into()));
    let sent=task.await.unwrap();
    assert_eq!(sent[1]["messages"][1]["tool_calls"][0]["id"],"call-x");
    assert_eq!(sent[1]["messages"][2]["tool_call_id"],"call-x");
    assert_eq!(sent[1]["messages"][2]["role"],"tool");
}
#[tokio::test]
async fn rejects_truncated_generation() {
    let (host,task)=serve(vec![json!({"choices":[{"finish_reason":"length","message":{"role":"assistant","content":"partial"}}]})]).await;
    let mut model=LlamaCpp::new(config(host)).unwrap();
    assert!(model.next(&user(),&manual()).await.unwrap_err().contains("truncated"));
    task.await.unwrap();
}
#[tokio::test]
async fn rejects_invalid_tool_call_arguments() {
    let (host,task)=serve(vec![json!({"choices":[{"message":{"role":"assistant","content":"","tool_calls":[{"function":{"name":"echo","arguments":"{bad"}}]}}]})]).await;
    let mut model=LlamaCpp::new(config(host)).unwrap();
    assert!(model.next(&user(),&manual()).await.unwrap_err().contains("invalid JSON"));
    task.await.unwrap();
}
