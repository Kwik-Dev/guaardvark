import { describe, expect, it } from "vitest";
import axios from "axios";
import { handleResponse } from "../apiClient";
import {
  dispatchWarning,
  installQueueMessages,
  QUEUE_UNREACHABLE_MESSAGE,
} from "../taskQueue";

const SERVER = "Task x.run was not started: Guaardvark's background queue (Redis at localhost:6379) is not reachable (Error 111).";

const jsonResponse = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const replying = (status, data) => {
  const instance = axios.create();
  instance.defaults.adapter = async (config) => {
    const error = new Error(`Request failed with status code ${status}`);
    error.config = config;
    error.response = { status, data, headers: {}, config };
    throw error;
  };
  installQueueMessages({ axios: instance });
  return instance;
};

describe("a task the background queue did not take", () => {
  it("is worded for the UI on axios, keeping the server's text", async () => {
    const instance = replying(503, { error: SERVER, code: "task_queue_unreachable", task: "x.run" });
    const error = await instance.post("/api/music-video/1/generate-storyboards").catch((e) => e);
    expect(error.message).toBe(QUEUE_UNREACHABLE_MESSAGE);
    expect(error.queueUnreachable).toBe(true);
    expect(error.response.data.error).toBe(QUEUE_UNREACHABLE_MESSAGE);
    expect(error.response.data.server_error).toBe(SERVER);
  });

  it("is recognised in the nested error envelope too", async () => {
    const instance = replying(503, { success: false, error: { code: "task_queue_unreachable", message: SERVER } });
    const error = await instance.post("/api/video-overlay/render-timeline").catch((e) => e);
    expect(error.message).toBe(QUEUE_UNREACHABLE_MESSAGE);
  });

  it("leaves other 503s alone", async () => {
    const instance = replying(503, { error: "Cannot connect to master server", code: "SERVICE_UNAVAILABLE" });
    const error = await instance.get("/api/interconnector/status").catch((e) => e);
    expect(error.queueUnreachable).toBeUndefined();
    expect(error.response.data.error).toBe("Cannot connect to master server");
  });

  it("is worded for the UI by handleResponse", async () => {
    await expect(
      handleResponse(jsonResponse(503, { error: { code: "task_queue_unreachable", message: SERVER } }), { quiet: true }),
    ).rejects.toMatchObject({ status: 503, queueUnreachable: true, message: QUEUE_UNREACHABLE_MESSAGE });
  });
});

describe("dispatchWarning", () => {
  it("returns the warning only when the step was not queued", () => {
    expect(dispatchWarning({ dispatched: false, warning: "The analyzer was not started: x" })).toBe(
      "The analyzer was not started: x",
    );
    expect(dispatchWarning({ dispatched: true })).toBeNull();
    expect(dispatchWarning({ id: 3 })).toBeNull();
    expect(dispatchWarning(null)).toBeNull();
    expect(dispatchWarning({ dispatched: false })).toMatch(/not started/);
  });
});
