import type { RangeJob, RangeResponse } from "./types";
import type { PointDiagnosticResponse, RangeRequestPayload } from "./api";

type ProgressHandler = (percent: number, stage: string) => void;
type PartialHandler = (key: string, band: unknown) => void;

interface PendingCall {
  resolve: (value: unknown) => void;
  reject: (reason: unknown) => void;
  onProgress?: ProgressHandler;
  onPartial?: PartialHandler;
}

class BrowserEngineClient {
  private worker: Worker;
  private nextId = 1;
  private pending = new Map<number, PendingCall>();

  constructor() {
    this.worker = new Worker(new URL("./browserEngine.worker.ts", import.meta.url), { type: "module" });
    this.worker.onmessage = (event) => {
      const message = event.data;
      const pending = this.pending.get(message.id);
      if (!pending) return;
      if (message.type === "progress") {
        pending.onProgress?.(message.percent, message.stage);
      } else if (message.type === "partial") {
        pending.onPartial?.(message.key, message.band);
      } else if (message.type === "result") {
        this.pending.delete(message.id);
        pending.resolve(message.value);
      } else if (message.type === "error") {
        this.pending.delete(message.id);
        pending.reject(Object.assign(new Error(message.message), { status: message.status, body: message.body }));
      }
    };
  }

  call<T>(action: string, payload: unknown = null, onProgress?: ProgressHandler, onPartial?: PartialHandler): Promise<T> {
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (value: unknown) => void, reject, onProgress, onPartial });
      this.worker.postMessage({ id, action, payload, baseUrl: import.meta.env.BASE_URL });
    });
  }
}

let client: BrowserEngineClient | null = null;
const jobs = new Map<string, RangeJob>();

function getClient(): BrowserEngineClient {
  client ??= new BrowserEngineClient();
  return client;
}

export function browserCall<T>(action: string, payload?: unknown): Promise<T> {
  return getClient().call<T>(action, payload);
}

export function startBrowserRangeJob(payload: RangeRequestPayload): Promise<RangeJob> {
  const jobId = crypto.randomUUID();
  const initial: RangeJob = {
    jobId,
    status: "queued",
    progress: 0,
    stage: "Loading calculation engine",
    result: null,
    error: null,
    partialBands: {},
  };
  jobs.set(jobId, initial);

  void getClient().call<RangeResponse>(
    "range",
    payload,
    (progress, stage) => {
      const current = jobs.get(jobId);
      if (current) jobs.set(jobId, { ...current, status: "running", progress, stage });
    },
    (key, band) => {
      const current = jobs.get(jobId);
      if (current) jobs.set(jobId, {
        ...current,
        partialBands: { ...(current.partialBands ?? {}), [key]: band as never },
      });
    },
  ).then((result) => {
    const current = jobs.get(jobId) ?? initial;
    jobs.set(jobId, { ...current, status: "complete", progress: 100, stage: "Range calculation complete", result });
  }).catch((error) => {
    const current = jobs.get(jobId) ?? initial;
    jobs.set(jobId, { ...current, status: "failed", stage: "Calculation failed", error: error instanceof Error ? error.message : String(error) });
  });

  return Promise.resolve(initial);
}

export function getBrowserRangeJob(jobId: string): Promise<RangeJob> {
  const job = jobs.get(jobId);
  if (!job) return Promise.reject(new Error("Unknown or expired range job"));
  return Promise.resolve(job);
}

export function browserPointDiagnostic(payload: RangeRequestPayload & { point: { lat: number; lon: number } }): Promise<PointDiagnosticResponse> {
  return browserCall<PointDiagnosticResponse>("classify-point", payload);
}
