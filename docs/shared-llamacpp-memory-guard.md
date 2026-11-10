# Shared llama.cpp memory guard

The portable launcher uses `LLAMACPP_MEMORY_GUARD_ENABLED: true` by default.
This makes **127.0.0.1:8080 a shared HTTP gateway** for MAI, OpenClaw,
and any other local client configured to use that URL. The underlying
llama.cpp router binds to **127.0.0.1:8081** and retains `--models-max 1`.

Configuration (`settings.yaml`, restart required):

```yaml
LLAMACPP_MEMORY_GUARD_ENABLED: true
LLAMACPP_MIN_FREE_VRAM_MIB: 1024
MAI_MIN_AVAILABLE_RAM_MIB: 6144
LLAMACPP_CONTEXT_SIZE: 65536
```

- **RAM:** The gateway measures host available *physical* RAM before any
  model-triggering HTTP request. If below 6144 MiB, the request receives
  HTTP 503 instead of reaching llama.cpp.
- **GPU:** Before an unloaded model can load, the gateway obtains
  `/models` measurement metadata and requires that estimated free GPU
  memory minus model, context, compute, and an extra 1024 MiB overhead
  remains at least the configured 1024 MiB. Missing or failed measurements
  block the request. These are *snapshots*, not real-time VRAM quotas.
- **Monitoring:** During an accepted request the gateway records available
  RAM samples every two seconds in `MAI_MEMORY/gpu_ram_safety.jsonl`.
  Monitoring is diagnostic; it does not forcibly stop an in-flight inference.
- **Concurrency:** Model-triggering requests and explicit unloads are
  serialized at the gateway to avoid cross-client unloads during inference.

Setting `LLAMACPP_MEMORY_GUARD_ENABLED: false` **disables the memory
admission checks, sampling and gateway**. The llama.cpp router then binds
directly to 8080, as before. The single-resident model policy remains in
force. There is no second independent MAI-only RAM/VRAM check.

Important limits:
- **Clients must use port 8080** to receive common protection. Port 8081
  is loopback-only but directly accessible to other software on the host;
  it is *not* an OS-level access control boundary.
- The gateway is not a global hardware RAM/VRAM allocation cap; external
  processes and driver allocations can change memory after preflight.
- Clients must include the model ID in requests that might trigger loading.
  Ambiguous model-triggering requests fail closed with HTTP 400.
- This gateway is specific to the portable launcher. A standalone OpenClaw
  process using a separate Ollama or llama.cpp server is **not protected**.
- When enabled, clients keep using the existing OpenAI-compatible URL
  `http://127.0.0.1:8080/v1`. Do not switch clients to 8081.

To verify after startup: query `http://127.0.0.1:8080/models` and inspect
`MAI_MEMORY/gpu_ram_safety.jsonl` after a short inference.


## 게이트웨이 POST 잠금 및 오류 진단

- 모델 요청과 모델 해제 요청은 공통 잠금으로 직렬화한다. **잠금 획득 대기시간 기본값은 30초**이며, 초과하면 HTTP 503과 `Gateway model request lock wait timed out` 오류를 반환한다. 이미 실행 중인 요청의 잠금을 강제로 해제하거나 다른 모델을 로딩하지 않는다.
- 잠금을 획득했을 때는 `Gateway model request lock acquired`, 반환했을 때는 `Gateway model request lock released` 로그가 기록된다. 각각 대기시간과 보유시간을 초 단위로 표시한다.
- 스트리밍 요청의 자원 정리는 응답 전송 전체의 생명주기에 연결한다. 클라이언트 연결이 끊기거나 스트리밍 시작 전 전송에 실패하더라도 상위 HTTP 응답 종료·메모리 감시 작업 취소·잠금 해제를 수행한다.
- 기본 잠금 대기시간 변경 시 게이트웨이 실행 명령에 `--lock-wait-timeout-seconds <초>` 옵션을 지정할 수 있다. 값은 0보다 커야 한다.
- 응답이 없는 원인을 분석할 때는 8080 게이트웨이의 잠금 획득·해제 로그를 우선 비교하고, 필요할 때만 8081 Router와 모델 자식 프로세스의 상태를 분리해서 진단한다. 8081 직접 호출은 공통 메모리 보호를 우회하므로 운영용으로 사용하지 않는다.
