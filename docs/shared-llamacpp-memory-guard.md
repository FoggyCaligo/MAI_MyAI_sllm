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
