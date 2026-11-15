use mai_runtime::supervisor::{ModelSupervisor,Policy,RamProbe};
use serde_json::{json,Value};
use std::{sync::{Arc,atomic::{AtomicBool,Ordering}},time::Duration};
use tokio::{io::{AsyncReadExt,AsyncWriteExt},net::TcpListener,sync::Mutex};
use tokio_util::sync::CancellationToken;
const MIB:u64=1_048_576;

struct FixedRam(Option<u64>);
impl RamProbe for FixedRam {
    fn available_bytes(&self)->Option<u64>{self.0}
}
fn policy(host:String)->Policy{
    let mut p=Policy::new(host);
    p.poll=Duration::from_millis(2);
    p.unload_timeout=Duration::from_millis(30);
    p.load_timeout=Duration::from_millis(30);
    p
}
fn model(id:&str,status:&str)->Value{
    json!({"id":id,"status":{"value":status},"memory":{
        "status":"measured",
        "max":{"devices":[{"kind":"device","name":"gpu",
            "free":16000*MIB,"model":8000*MIB,
            "context":2000*MIB,"compute":1000*MIB}]}
    }})
}
async fn server(freeze_unload:bool)->(String,Arc<Mutex<Vec<String>>>,tokio::task::JoinHandle<()>) {
    let listener=TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr=format!("http://{}",listener.local_addr().unwrap());
    let calls=Arc::new(Mutex::new(Vec::<String>::new()));
    let log=calls.clone();
    let a=Arc::new(AtomicBool::new(true));
    let b=Arc::new(AtomicBool::new(false));
    let task=tokio::spawn(async move{
        loop{
            let Ok((mut stream,_))=listener.accept().await else{break};
            let mut buf=[0u8;8192];
            let Ok(count)=stream.read(&mut buf).await else{continue};
            let line=String::from_utf8_lossy(&buf[..count]);
            let path=line.lines().next().unwrap_or("").split_whitespace().nth(1).unwrap_or("");
            log.lock().await.push(path.to_string());
            let response=match path{
                "/models"=>json!({"data":[
                    model("A",if a.load(Ordering::SeqCst){"loaded"}else{"unloaded"}),
                    model("B",if b.load(Ordering::SeqCst){"loaded"}else{"unloaded"})
                ]}),
                "/models/unload"=>{
                    if !freeze_unload{a.store(false,Ordering::SeqCst);}
                    json!({"success":true})
                }
                "/models/load"=>{
                    b.store(true,Ordering::SeqCst);
                    json!({"success":true})
                }
                _=>json!({"error":"unexpected"})
            };
            let body=response.to_string();
            let data=format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",body.len(),body);
            let _=stream.write_all(data.as_bytes()).await;
        }
    });
    (addr,calls,task)
}
#[tokio::test]
async fn confirms_unload_before_load_and_serializes_inference(){
    let (url,calls,task)=server(false).await;
    let manager=ModelSupervisor::new(policy(url),Arc::new(FixedRam(Some(12_000*MIB)))).unwrap();
    let token=CancellationToken::new();
    let guard=manager.acquire("B",&token).await.unwrap();
    let list=calls.lock().await.clone();
    let u=list.iter().position(|x|x=="/models/unload").unwrap();
    let l=list.iter().position(|x|x=="/models/load").unwrap();
    assert!(u<l);
    let wait=tokio::time::timeout(Duration::from_millis(5),manager.acquire("B",&token)).await;
    assert!(wait.is_err(),"a second inference must wait while first holds lock");
    drop(guard);
    let _next=manager.acquire("B",&token).await.unwrap();
    assert_eq!(calls.lock().await.iter().filter(|x|*x=="/models/load").count(),1);
    task.abort();
}
#[tokio::test]
async fn unknown_ram_fails_closed_without_router_calls(){
    let (url,calls,task)=server(false).await;
    let manager=ModelSupervisor::new(policy(url),Arc::new(FixedRam(None))).unwrap();
    let err=manager.acquire("B",&CancellationToken::new()).await.err().unwrap();
    assert!(err.contains("RAM measurement unavailable"));
    assert!(calls.lock().await.is_empty());
    task.abort();
}
#[tokio::test]
async fn unconfirmed_unload_prevents_next_load(){
    let (url,calls,task)=server(true).await;
    let manager=ModelSupervisor::new(policy(url),Arc::new(FixedRam(Some(12_000*MIB)))).unwrap();
    assert!(manager.acquire("B",&CancellationToken::new()).await.is_err());
    assert!(!calls.lock().await.contains(&"/models/load".to_string()));
    task.abort();
}
#[test]
fn rejects_gpu_unknown_or_insufficient_budget(){
    let manager=ModelSupervisor::new(policy("http://127.0.0.1:1".into()),
        Arc::new(FixedRam(Some(12_000*MIB)))).unwrap();
    let mut m=model("B","unloaded");
    assert!(manager.validate_vram(&m).is_ok());
    m["memory"]["status"]=json!("failed");
    assert!(manager.validate_vram(&m).is_err());
    m["memory"]["status"]=json!("measured");
    m["memory"]["max"]["devices"][0]["free"]=json!(12_000*MIB);
    assert!(manager.validate_vram(&m).is_err());
}
