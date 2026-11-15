//! MAI Web API PREVIEW. Loopback only; not a production MAI replacement.
//! Existing HTML/JS is served unchanged from the preserved python/ tree.
use axum::{
    extract::{Path, State}, http::{HeaderMap, StatusCode},
    response::{Html, IntoResponse}, routing::{get, post}, Json, Router,
};
use mai_runtime::{
    llamacpp::{LlamaConfig, LlamaCpp}, Engine, Event, Registry,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap, env, path::PathBuf, sync::Arc,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tokio::sync::{mpsc, Mutex};
use tokio_util::sync::CancellationToken;
use tower_http::services::ServeDir;
use uuid::Uuid;

type Reply = (StatusCode, Json<Value>);
fn success(value: Value) -> Reply { (StatusCode::OK, Json(value)) }
fn failure(status: StatusCode, reason: &str) -> Reply {
    (status, Json(json!({"detail":reason})))
}
fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs_f64()
}
fn new_token() -> String { Uuid::new_v4().to_string() }

#[derive(Clone, Deserialize)]
struct User {
    user_id: String,
    user_pw: String,
    db_id: String,
}
#[derive(Clone)]
struct Config {
    model: String,
    llama_url: String,
    ui_dir: PathBuf,
}
struct PreAuth {
    until: Instant,
    failures: u8,
}
struct Job {
    owner: String,
    status: String,
    created: f64,
    updated: f64,
    response: Option<Value>,
    error: Option<String>,
    tools: Vec<Value>,
    round: Option<usize>,
    cancel: CancellationToken,
}
impl Job {
    fn snapshot(&self, id: &str) -> Value {
        json!({
            "job_id":id,"status":self.status,"created_at":self.created,"updated_at":self.updated,
            "response":self.response,"error":self.error,"tools":self.tools,
            "model_round":self.round,"thinking":null,"stream_progress":null,
            "verification_rejections":[]
        })
    }
}
struct Data {
    preauth: HashMap<String, PreAuth>,
    sessions: HashMap<String, String>, // auth bearer => user id
    jobs: HashMap<String, Job>,
    history: HashMap<(String, String), Vec<Value>>,
}
#[derive(Clone)]
struct App {
    config: Arc<Config>,
    users: Arc<Vec<User>>,
    data: Arc<Mutex<Data>>,
}
impl App {
    fn new(config: Config, users: Vec<User>) -> Self {
        Self {
            config: Arc::new(config),
            users: Arc::new(users),
            data: Arc::new(Mutex::new(Data {
                preauth:HashMap::new(), sessions:HashMap::new(),
                jobs:HashMap::new(), history:HashMap::new(),
            })),
        }
    }
    async fn auth(&self, headers: &HeaderMap) -> Result<String, Reply> {
        let header = headers.get("authorization").and_then(|v|v.to_str().ok()).unwrap_or("");
        let token = header.strip_prefix("Bearer ")
            .filter(|token|!token.is_empty())
            .ok_or_else(||failure(StatusCode::UNAUTHORIZED,"authentication required"))?;
        let data=self.data.lock().await;
        data.sessions.get(token).cloned()
            .ok_or_else(||failure(StatusCode::UNAUTHORIZED,"invalid authorization token"))
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Credentials { user_id: String, user_pw: String }
#[derive(Deserialize)]
struct ChatInput {
    message: String,
    #[serde(default="default_session")] session_id: String,
    model: Option<String>,
    remote_session_id: Option<String>,
    #[serde(default)] attachments: Vec<String>,
    #[allow(dead_code)]
    verify: Option<bool>, // legacy UI sends this; intentionally no Verifier
}
fn default_session()->String{"default".into()}

async fn home(State(app): State<App>) -> impl IntoResponse {
    let path=app.config.ui_dir.join("index.html");
    let mut html=match tokio::fs::read_to_string(path).await {
        Ok(html)=>html,Err(_)=>return failure(StatusCode::SERVICE_UNAVAILABLE,"UI files unavailable").into_response()
    };
    // Match legacy runtime injection without editing the UI source.
    let subtitle="<div class=\"subtitle\">local personal agent</div>";
    let remote_marker="<button id=\"remote-btn\" type=\"button\" hidden aria-expanded=\"false\" aria-controls=\"remote-panel\" data-mai-icon=\"monitor\" data-mai-label=\"Remote connection\"></button>";
    let new_chat="<button id=\"new-chat-btn\" type=\"button\" data-mai-icon=\"message-plus\" data-mai-label=\"New chat\" title=\"New chat (keeps long-term memory)\"></button>";
    let markers=[subtitle,remote_marker,"<form id=\"chat-form\">","<script src=\"/static/voice-input.js\"></script>","</body>"];
    if markers.iter().any(|marker|!html.contains(marker)) {
        return failure(StatusCode::SERVICE_UNAVAILABLE,"UI compatibility marker missing").into_response();
    }
    html=html.replacen(subtitle,"<div class=\"subtitle\">My - AI</div>",1);
    html=html.replacen(remote_marker,&format!("{new_chat}{remote_marker}"),1);
    html=html.replacen("<form id=\"chat-form\">","<form id=\"chat-form\" novalidate>",1);
    html=html.replacen("</body>",
        "<script src=\"/static/resumable-chat.js\"></script>\n<script src=\"/static/new-chat.js\"></script>\n</body>",1);
    Html(html).into_response()
}
async fn health() -> Reply { success(json!({"status":"ok","mode":"rust-preview"})) }
async fn login_session(State(app):State<App>)->Reply {
    let token=new_token();
    app.data.lock().await.preauth.insert(token.clone(),PreAuth{
        until:Instant::now()+Duration::from_secs(300),failures:0,
    });
    success(json!({"session_token":token}))
}
async fn login(
    State(app):State<App>,headers:HeaderMap,Json(input):Json<Credentials>,
)->Reply {
    let preauth=headers.get("x-mai-preauth-session").and_then(|v|v.to_str().ok()).unwrap_or("");
    let mut data=app.data.lock().await;
    let Some(pre)=data.preauth.get_mut(preauth) else {
        return failure(StatusCode::BAD_REQUEST,"pre-login browser session required");
    };
    if Instant::now()>=pre.until {
        data.preauth.remove(preauth);
        return failure(StatusCode::BAD_REQUEST,"pre-login browser session expired");
    }
    if pre.failures>=5 {
        return failure(StatusCode::TOO_MANY_REQUESTS,"Too many failed login attempts");
    }
    let principal=app.users.iter().find(|u|u.user_id==input.user_id && u.user_pw==input.user_pw);
    let Some(principal)=principal else {
        pre.failures+=1;
        return failure(StatusCode::FORBIDDEN,"invalid credentials");
    };
    // Single current login per user, matching legacy re-login behavior.
    data.preauth.remove(preauth);
    data.sessions.retain(|_,id|id!=&principal.user_id);
    let token=new_token();
    data.sessions.insert(token.clone(),principal.user_id.clone());
    success(json!({"token":token,"user_id":principal.user_id,"db_id":principal.db_id,"role":"owner"}))
}
async fn me(State(app):State<App>,headers:HeaderMap)->Reply {
    let user=match app.auth(&headers).await{Ok(x)=>x,Err(e)=>return e};
    let Some(p)=app.users.iter().find(|p|p.user_id==user) else {
        return failure(StatusCode::UNAUTHORIZED,"unknown user");
    };
    success(json!({"user_id":p.user_id,"db_id":p.db_id,"role":"owner"}))
}
async fn logout(State(app):State<App>,headers:HeaderMap)->Reply {
    let token=headers.get("authorization").and_then(|v|v.to_str().ok())
        .and_then(|s|s.strip_prefix("Bearer ")).unwrap_or("");
    if app.auth(&headers).await.is_err() {
        return failure(StatusCode::UNAUTHORIZED,"authentication required");
    }
    app.data.lock().await.sessions.remove(token);
    success(json!({"logged_out":true}))
}
async fn models(State(app):State<App>,headers:HeaderMap)->Reply {
    if let Err(e)=app.auth(&headers).await{return e;}
    // This preview does not select/load models. Verify runtime availability.
    let client=match reqwest::Client::builder().timeout(Duration::from_secs(5)).build(){
        Ok(c)=>c,Err(_)=>return failure(StatusCode::SERVICE_UNAVAILABLE,"HTTP client unavailable")
    };
    let url=format!("{}/models",app.config.llama_url.trim_end_matches('/'));
    let data:Value=match client.get(url).send().await{
        Ok(response)=>match response.json().await {
            Ok(v)=>v,Err(_)=>return failure(StatusCode::SERVICE_UNAVAILABLE,"invalid llama.cpp model list")
        },
        Err(_)=>return failure(StatusCode::SERVICE_UNAVAILABLE,"llama.cpp router unavailable"),
    };
    let available=data.get("data").and_then(Value::as_array);
    let loaded=available.is_some_and(|arr|arr.iter().any(|x|{
        x.get("id").and_then(Value::as_str)==Some(&app.config.model)
        && x.get("status").and_then(|s|s.get("value")).and_then(Value::as_str)==Some("loaded")
    }));
    if !loaded {
        return failure(StatusCode::SERVICE_UNAVAILABLE,"selected llama.cpp model is not loaded");
    }
    success(json!({
        "models":[app.config.model],"current":app.config.model,
        "locked":true,"labels":{app.config.model.clone():app.config.model.clone()},
        "backend":"llamacpp"
    }))
}
async fn model_select(State(app):State<App>,headers:HeaderMap,Json(input):Json<Value>)->Reply {
    if let Err(e)=app.auth(&headers).await{return e;}
    if input.get("model").and_then(Value::as_str)==Some(&app.config.model) {
        return success(json!({"model":app.config.model,"status":"selected"}));
    }
    failure(StatusCode::NOT_IMPLEMENTED,"model switching requires Rust ModelSupervisor")
}

async fn submit_job(State(app):State<App>,headers:HeaderMap,Json(input):Json<ChatInput>)->Reply {
    let user=match app.auth(&headers).await{Ok(x)=>x,Err(e)=>return e};
    if input.message.trim().is_empty() || input.session_id.trim().is_empty(){
        return failure(StatusCode::UNPROCESSABLE_ENTITY,"empty prompt/session");
    }
    if input.model.as_ref().is_some_and(|m|m!=&app.config.model) {
        return failure(StatusCode::NOT_IMPLEMENTED,"model switching unavailable in preview");
    }
    if input.remote_session_id.is_some() || !input.attachments.is_empty(){
        return failure(StatusCode::NOT_IMPLEMENTED,"remote workers and attachments are not migrated yet");
    }
    let job_id=new_token();
    let cancel=CancellationToken::new();
    {
        let mut data=app.data.lock().await;
        for job in data.jobs.values_mut().filter(|j|j.owner==user && (j.status=="pending"||j.status=="running")){
            job.cancel.cancel();
        }
        data.history.entry((user.clone(),input.session_id.clone())).or_default()
            .push(json!({"role":"user","content":input.message,"created_at":now()}));
        data.jobs.insert(job_id.clone(),Job {
            owner:user.clone(),status:"pending".into(),created:now(),updated:now(),
            response:None,error:None,tools:vec![],round:None,cancel:cancel.clone(),
        });
    }
    tokio::spawn(run_job(app.clone(),job_id.clone(),user,input.session_id,input.message,cancel));
    (StatusCode::OK,Json(json!({"job_id":job_id,"status":"pending"})))
}
async fn run_job(app:App,id:String,user:String,session:String,message:String,cancel:CancellationToken){
    {
        let mut data=app.data.lock().await;
        if let Some(job)=data.jobs.get_mut(&id){job.status="running".into();job.updated=now();}
    }
    let config=LlamaConfig{
        endpoint:app.config.llama_url.clone(),model:app.config.model.clone(),
        max_output_tokens:32768,
    };
    let (tx,mut rx)=mpsc::channel(128);
    let task=async {
        let model=LlamaCpp::new(config).map_err(|err|err.to_string())?;
        let mut engine=Engine::new(model,Registry::new(),Duration::from_secs(500))
            .map_err(|err|err.to_string())?;
        engine.run(&message,&cancel,&tx).await.map_err(|err|format!("{err:?}"))
    };
    tokio::pin!(task);
    let mut outcome=None;
    loop{
        tokio::select!{
            result=&mut task=>{
                outcome=Some(result);
                break;
            }
            event=rx.recv()=>{
                if let Some(event)=event {
                    let mut data=app.data.lock().await;
                    if let Some(job)=data.jobs.get_mut(&id) {
                        match event {
                            Event::Round{number,..}=>job.round=Some(number),
                            Event::Tool{name,ok,result}=>job.tools.push(json!({
                                "name":name,"arguments":{},"ok":ok,
                                "error_type":if ok {Value::Null}else{json!("ToolError")},
                                "result":result.to_string(),
                            })),
                            _=>{}
                        }
                        job.updated=now();
                    }
                }
            }
        }
    }
    let mut data=app.data.lock().await;
    let Some(job)=data.jobs.get_mut(&id) else{return};
    job.updated=now();
    if cancel.is_cancelled() {
        job.status="cancelled".into();
        job.error=Some("cancelled".into());
    }else if let Some(Ok(result))=outcome {
        job.status="completed".into();
        job.response=Some(json!({
            "answer":result.answer,"model":app.config.model,"model_rounds":result.rounds,
            "tools":job.tools,"verification_rejections":[]
        }));
        data.history.entry((user,session)).or_default()
            .push(json!({"role":"assistant","content":result.answer,"created_at":now()}));
    }else{
        job.status="failed".into();
        job.error=Some(outcome.and_then(Result::err).unwrap_or_else(||"runtime finished without result".into()));
    }
}
async fn active_jobs(State(app):State<App>,headers:HeaderMap)->Reply{
    let user=match app.auth(&headers).await{Ok(x)=>x,Err(e)=>return e};
    let data=app.data.lock().await;
    success(json!({"jobs":data.jobs.iter().filter(|(_,j)|j.owner==user &&(j.status=="pending"||j.status=="running"))
        .map(|(id,j)|j.snapshot(id)).collect::<Vec<_>>()}))
}
async fn get_job(State(app):State<App>,Path(id):Path<String>,headers:HeaderMap)->Reply{
    let user=match app.auth(&headers).await{Ok(x)=>x,Err(e)=>return e};
    let data=app.data.lock().await;
    match data.jobs.get(&id).filter(|j|j.owner==user){
        Some(job)=>success(job.snapshot(&id)),None=>failure(StatusCode::NOT_FOUND,"chat job not found")
    }
}
async fn cancel_job(State(app):State<App>,Path(id):Path<String>,headers:HeaderMap)->Reply{
    let user=match app.auth(&headers).await{Ok(x)=>x,Err(e)=>return e};
    let mut data=app.data.lock().await;
    match data.jobs.get_mut(&id).filter(|j|j.owner==user){
        Some(job)=>{
            let active=job.status=="pending"||job.status=="running";
            if active {job.cancel.cancel();}
            success(json!({"cancelled":active,"job":job.snapshot(&id)}))
        },
        None=>failure(StatusCode::NOT_FOUND,"chat job not found")
    }
}
async fn session_history(State(app):State<App>,Path(id):Path<String>,headers:HeaderMap)->Reply{
    let user=match app.auth(&headers).await{Ok(x)=>x,Err(e)=>return e};
    let data=app.data.lock().await;
    success(json!({"session_id":id,"messages":data.history.get(&(user,id.clone())).cloned().unwrap_or_default()}))
}
async fn clear_session(State(app):State<App>,Path(id):Path<String>,headers:HeaderMap)->Reply{
    let user=match app.auth(&headers).await{Ok(x)=>x,Err(e)=>return e};
    let mut data=app.data.lock().await;
    data.history.remove(&(user,id.clone()));
    success(json!({"session_id":id,"cleared":true}))
}

fn router(app:App)->Router{
    Router::new()
    .route("/",get(home))
    .route("/health",get(health))
    .route("/login/session",get(login_session))
    .route("/login",post(login))
    .route("/me",get(me))
    .route("/logout",post(logout))
    .route("/models",get(models))
    .route("/models/select",post(model_select))
    .route("/chat/jobs",post(submit_job))
    .route("/chat/jobs/active",get(active_jobs))
    .route("/chat/jobs/{id}",get(get_job).delete(cancel_job))
    .route("/session/{id}",get(session_history).delete(clear_session))
    .nest_service("/static",ServeDir::new(app.config.ui_dir.clone()))
    .with_state(app)
}

#[tokio::main]
async fn main()->Result<(),Box<dyn std::error::Error>>{
    // Explicitly limited to loopback while auth/persistence/worker parity remains incomplete.
    let users:Vec<User>=serde_json::from_str(&env::var("OWNER_USERS")
        .map_err(|_|"OWNER_USERS environment variable required")?)?;
    if users.is_empty() || users.iter().any(|u|u.user_id.is_empty()||u.user_pw.is_empty()||u.db_id.is_empty()){
        return Err("OWNER_USERS must contain valid nonempty user records".into());
    }
    let model=env::var("MAI_RUST_MODEL").map_err(|_|"MAI_RUST_MODEL required")?;
    let port:u16=env::var("MAI_RUST_PORT").unwrap_or_else(|_|"8100".into()).parse()?;
    let ui=env::var("MAI_RUST_UI_DIR").map(PathBuf::from)
        .unwrap_or_else(|_|PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../python/mai/app/static"));
    let config=Config{model,llama_url:env::var("LLAMACPP_HOST")
        .unwrap_or_else(|_|"http://127.0.0.1:8080".into()),ui_dir:ui};
    let state=App::new(config,users);
    let listener=tokio::net::TcpListener::bind(("127.0.0.1",port)).await?;
    eprintln!("MAI Rust preview listening only on http://127.0.0.1:{port} (not production-ready)");
    axum::serve(listener,router(state)).await?;
    Ok(())
}

#[cfg(test)]
mod tests{
    use super::*;
    use axum::{body::Body,http::{Request,StatusCode}};
    use tower::ServiceExt;
    fn test_app()->Router{
        router(App::new(Config{
            model:"test".into(),llama_url:"http://127.0.0.1:0".into(),
            ui_dir:PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../python/mai/app/static")
        },vec![User{user_id:"owner".into(),user_pw:"secret".into(),db_id:"unit".into()}]))
    }
    #[tokio::test]
    async fn private_chat_job_cannot_be_submitted_without_login(){
        let response=test_app().oneshot(Request::builder().method("POST")
            .uri("/chat/jobs").header("content-type","application/json")
            .body(Body::from(r#"{"message":"hello"}"#)).unwrap()).await.unwrap();
        assert_eq!(response.status(),StatusCode::UNAUTHORIZED);
    }
    #[tokio::test]
    async fn legacy_login_requires_pre_auth_token(){
        let response=test_app().oneshot(Request::builder().method("POST")
            .uri("/login").header("content-type","application/json")
            .body(Body::from(r#"{"user_id":"owner","user_pw":"secret"}"#)).unwrap()).await.unwrap();
        assert_eq!(response.status(),StatusCode::BAD_REQUEST);
    }
    #[tokio::test]
    async fn health_is_loopback_preview(){
        let response=test_app().oneshot(Request::builder().uri("/health")
            .body(Body::empty()).unwrap()).await.unwrap();
        assert_eq!(response.status(),StatusCode::OK);
    }
}
