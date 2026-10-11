import { describe, it, expect } from "vitest";
import { comfyUiLink } from "./comfyUiLink";

describe("comfyUiLink", () => {
  it("opens ComfyUI on the host the page was opened on, at the backend's port", () => {
    expect(comfyUiLink("192.168.1.20", "http://localhost:8188")).toBe("http://192.168.1.20:8188");
    expect(comfyUiLink("studio.local", "http://127.0.0.1:8000")).toBe("http://studio.local:8000");
  });

  it("keeps 8188 when the backend has not reported an address", () => {
    expect(comfyUiLink("192.168.1.20", null)).toBe("http://192.168.1.20:8188");
    expect(comfyUiLink("localhost", undefined)).toBe("http://localhost:8188");
    expect(comfyUiLink("localhost", "not a url")).toBe("http://localhost:8188");
  });

  it("opens an address that already names a machine as given", () => {
    expect(comfyUiLink("192.168.1.20", "http://10.0.0.5:8188")).toBe("http://10.0.0.5:8188");
    expect(comfyUiLink("192.168.1.20", "http://comfy.lan:9000/")).toBe("http://comfy.lan:9000");
  });

  it("brackets an IPv6 page host", () => {
    expect(comfyUiLink("fe80::1", "http://localhost:8188")).toBe("http://[fe80::1]:8188");
    expect(comfyUiLink("[::1]", "http://localhost:8188")).toBe("http://[::1]:8188");
  });
});
