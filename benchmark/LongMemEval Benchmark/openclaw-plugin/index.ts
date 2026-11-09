import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { withSessionTranscriptWrite, readVisibleSessionTranscriptMessageEntries } from "openclaw/plugin-sdk/session-transcript-runtime";

type SourceTurn = { role: "user" | "assistant"; content: string };
type SourceSession = { session_id: string; timestamp_ms: number; turns: SourceTurn[] };
type ImportFile = { case_id: string; sessions: SourceSession[] };

function checkImport(value: unknown): ImportFile {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("LongMemEval import must be an object");
  }
  const record = value as Record<string, unknown>;
  if (typeof record.case_id !== "string" || !record.case_id) {
    throw new Error("LongMemEval import missing case_id");
  }
  if (!Array.isArray(record.sessions) || !record.sessions.length) {
    throw new Error("LongMemEval import must contain sessions");
  }
  const ids = new Set<string>();
  let prev = -Infinity;
  const sessions: SourceSession[] = [];
  for (const raw of record.sessions) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      throw new TypeError("LongMemEval session must be an object");
    }
    const session = raw as Record<string, unknown>;
    const id = session.session_id;
    const ts = session.timestamp_ms;
    const rawTurns = session.turns;
    if (typeof id !== "string" || !id || ids.has(id)) {
      throw new Error("LongMemEval requires unique nonempty session IDs");
    }
    if (typeof ts !== "number" || !Number.isFinite(ts) || ts < prev) {
      throw new Error("LongMemEval session timestamp invalid or out of chronological order");
    }
    if (!Array.isArray(rawTurns) || !rawTurns.length || rawTurns.length % 2) {
      throw new Error("LongMemEval sessions require paired user and assistant turns");
    }
    const turns: SourceTurn[] = [];
    for (let index = 0; index < rawTurns.length; index++) {
      const entry = rawTurns[index];
      if (!entry || typeof entry !== "object") {
        throw new TypeError("LongMemEval turn must be an object");
      }
      const turn = entry as Record<string, unknown>;
      const expected = index % 2 === 0 ? "user" : "assistant";
      if (turn.role !== expected || typeof turn.content !== "string" || !turn.content) {
        throw new Error("LongMemEval turn role/content contract violation");
      }
      turns.push({ role: expected, content: turn.content });
    }
    sessions.push({ session_id: id, timestamp_ms: ts, turns });
    ids.add(id);
    prev = ts;
  }
  return { case_id: record.case_id, sessions };
}

function transcriptKey(caseId: string, sessionId: string): string {
  const identity = createHash("sha256").update(JSON.stringify([caseId, sessionId])).digest("hex");
  return `agent:main:longmemeval:${identity}`;
}

export default definePluginEntry({
  id: "longmemeval-replay",
  name: "LongMemEval Native Replay",
  description: "Import benchmark history into native OpenClaw session transcripts",
  register(api) {
    api.registerCli(
      async ({ program, config }) => {
        program
          .command("longmemeval")
          .description("Strict, offline native role-preserving LongMemEval replay")
          .command("import")
          .requiredOption("--input <path>", "JSON file with sanitized original sessions; no gold answers")
          .action(async (options: { input: string }) => {
            const source = checkImport(JSON.parse(await readFile(options.input, "utf8")));
            let count = 0;
            for (const session of source.sessions) {
              const sessionKey = transcriptKey(source.case_id, session.session_id);
              const created = await api.runtime.agent.session.createSessionEntry({
                cfg: config,
                key: sessionKey,
                agentId: "main",
              });
              if (created.key !== sessionKey || created.agentId !== "main" || !created.sessionId) {
                throw new Error("OpenClaw created a session with unexpected identity");
              }
              await withSessionTranscriptWrite(
                {
                  agentId: created.agentId,
                  sessionId: created.sessionId,
                  sessionKey: created.key,
                  config,
                },
                async (transcript) => {
                  for (const [i, turn] of session.turns.entries()) {
                    const timestamp = session.timestamp_ms + i;
                    const message =
                      turn.role === "user"
                        ? { role: "user" as const, content: turn.content, timestamp }
                        : {
                            role: "assistant" as const,
                            content: [{ type: "text" as const, text: turn.content }],
                            timestamp,
                            api: "openai-responses" as const,
                            provider: "longmemeval-source",
                            model: "original-transcript",
                            usage: {
                              input: 0, output: 0, cacheRead: 0, cacheWrite: 0,
                              totalTokens: 0,
                              cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
                            },
                            stopReason: "stop" as const,
                          };
                    await transcript.appendMessage({
                      message: {
                        ...message,
                        idempotencyKey: `longmemeval:${source.case_id}:${session.session_id}:${i}`,
                      },
                      idempotencyLookup: "scan",
                    });
                  }
                },
              );
              const persisted = await readVisibleSessionTranscriptMessageEntries({
                agentId: created.agentId,
                sessionId: created.sessionId,
                sessionKey: created.key,
                config,
              });
              if (!Array.isArray(persisted) || persisted.length !== session.turns.length) {
                throw new Error(`Transcript persistence mismatch for ${session.session_id}`);
              }
              for (let i = 0; i < persisted.length; i++) {
                const entry = persisted[i];
                const expected = session.turns[i];
                const actual = entry.message;
                const content = actual.role === "assistant"
                  ? (Array.isArray(actual.content) ? actual.content
                      .filter((item) => item.type === "text")
                      .map((item) => item.text).join("") : null)
                  : actual.content;
                if (actual.role !== expected.role || content !== expected.content) {
                  throw new Error(`Transcript content/role mismatch at ${session.session_id} turn ${i}`);
                }
              }
              count += session.turns.length;
            }
            process.stdout.write(JSON.stringify({
              ok: true, case_id: source.case_id,
              sessions: source.sessions.length, messages: count,
              mode: "native_transcript_replay",
            }) + "\n");
          });
      },
      {
        descriptors: [{
          name: "longmemeval",
          description: "Import original LongMemEval dialogue into isolated OpenClaw native sessions",
          hasSubcommands: true,
        }],
      },
    );
  },
});
