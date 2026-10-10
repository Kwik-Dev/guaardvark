import { describe, it, expect, vi } from "vitest";
import FrontendResourceManager, { stripPictureFields } from "./resource_manager";

// A chat picture as the composer sends it: a base64 body and a data-URL preview,
// each well over the queue's text cap on its own.
const big = (n) => "A".repeat(n);

describe("stripPictureFields", () => {
  it("drops every field that carries a picture and keeps the rest", () => {
    const stripped = stripPictureFields({
      isImageAnalysis: true,
      imageFileName: "a.png",
      imageFileNames: ["a.png", "b.png"],
      imageBase64: big(10),
      imagePreview: big(10),
      imagesBase64: [big(10), big(10)],
      imagePreviews: [big(10), big(10)],
    });
    expect(stripped).toEqual({
      isImageAnalysis: true,
      imageFileName: "a.png",
      imageFileNames: ["a.png", "b.png"],
    });
  });

  it("leaves non-objects alone", () => {
    expect(stripPictureFields(null)).toBe(null);
    expect(stripPictureFields(undefined)).toBe(undefined);
  });
});

describe("enqueueMessage with a picture turn", () => {
  const queued = async (voiceOptions) => {
    const manager = new FrontendResourceManager();
    const queueId = manager.createMessageQueue("chat", { maxLength: 100000 });
    const processor = vi.fn(async () => {});
    const result = await manager.enqueueMessage(queueId, {
      inputText: "what is in these?",
      file: null,
      voiceOptions,
      chatMode: "analyze",
      sessionId: "s1",
      projectId: null,
    }, { processor });
    return { result, processor };
  };

  it("queues a single big picture instead of refusing it as too large", async () => {
    const { result, processor } = await queued({
      isImageAnalysis: true,
      imageBase64: big(150000),
      imagePreview: `data:image/jpeg;base64,${big(150000)}`,
      // The composer sends the array fields on every picture turn, one entry
      // for a single picture.
      imagesBase64: [big(150000)],
      imagePreviews: [`data:image/jpeg;base64,${big(150000)}`],
      imageFileName: "photo.jpg",
      imageFileNames: ["photo.jpg"],
    });
    expect(result?.error).toBeUndefined();
    expect(processor).toHaveBeenCalledTimes(1);
    // The processor gets the message intact, pictures included.
    expect(processor.mock.calls[0][0].voiceOptions.imagesBase64).toHaveLength(1);
  });

  it("queues several big pictures the same way", async () => {
    const { result, processor } = await queued({
      isImageAnalysis: true,
      imageBase64: big(150000),
      imagePreview: `data:image/jpeg;base64,${big(150000)}`,
      imagesBase64: [big(150000), big(150000), big(150000), big(150000)],
      imagePreviews: Array(4).fill(`data:image/jpeg;base64,${big(150000)}`),
      imageFileNames: ["a.jpg", "b.jpg", "c.jpg", "d.jpg"],
    });
    expect(result?.error).toBeUndefined();
    expect(processor).toHaveBeenCalledTimes(1);
  });

  it("still refuses a message whose text alone is over the cap", async () => {
    const manager = new FrontendResourceManager();
    const queueId = manager.createMessageQueue("chat", { maxLength: 1000 });
    const processor = vi.fn(async () => {});
    const result = await manager.enqueueMessage(queueId, {
      inputText: big(2000),
      voiceOptions: { imageBase64: big(10) },
    }, { processor });
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/Message object too large/);
    expect(processor).not.toHaveBeenCalled();
  });
});
