import { describe, expect, it } from "vitest";
import { RouteType, routeToDetection } from "./useAgentRouter";

const fileRoute = (toolParams, toolName = "generate_file") => ({
  route_type: RouteType.FILE_GENERATION,
  tool_name: toolName,
  tool_params: toolParams,
  confidence: 0.8,
  reasoning: "Matched pattern: file_generation",
});

describe("routeToDetection file names", () => {
  it("proposes the extension the request asked for", () => {
    const out = routeToDetection(fileRoute({ extension: "py" }));
    expect(out.isCodeRequest).toBe(true);
    expect(out.filename).toMatch(/^generated_file_\d+\.py$/);
  });

  it("keeps a file name the person typed", () => {
    const out = routeToDetection(fileRoute({ filename: "notes.md", extension: "md" }));
    expect(out.filename).toBe("notes.md");
  });

  it("proposes a .txt when the request names no language or file", () => {
    expect(routeToDetection(fileRoute(null)).filename).toMatch(/^generated_file_\d+\.txt$/);
  });

  it("leaves codegen and CSV defaults as they were", () => {
    const code = routeToDetection({ ...fileRoute(null, "codegen"), route_type: RouteType.TOOL_DIRECT });
    expect(code.filename).toMatch(/^generated_code_\d+\.js$/);
    const csv = routeToDetection({ ...fileRoute(null, "generate_csv"), route_type: RouteType.TOOL_DIRECT });
    expect(csv.isCSVRequest).toBe(true);
    expect(csv.filename).toMatch(/^generated_data_\d+\.csv$/);
  });

  it("is not a file request for plain chat", () => {
    expect(routeToDetection({ route_type: RouteType.CHAT_ONLY })).toEqual({
      isCSVRequest: false,
      isCodeRequest: false,
    });
  });
});
