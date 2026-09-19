import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  CHAT_ATTACHMENT_JPEG_QUALITY,
  CHAT_ATTACHMENT_MAX_BYTES_FALLBACK,
  CHAT_ATTACHMENT_MAX_EDGE,
  attachmentExceedsLimit,
  chatErrorMessage,
  downscaleChatAttachment,
  fetchAttachmentMaxBytes,
  formatAttachmentSize,
  refuseAttachmentMessage,
  resetAttachmentMaxBytesCache,
  resolveAttachmentMaxBytes,
} from "./chatAttachment";

function opaqueImageData(width, height) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < data.length; i += 4) {
    data[i] = 10;
    data[i + 1] = 20;
    data[i + 2] = 30;
    data[i + 3] = 255;
  }
  return { data, width, height };
}

function transparentImageData(width, height) {
  const { data } = opaqueImageData(width, height);
  data[3] = 128;
  return { data, width, height };
}

function stubCanvas({ transparent = false, blobSize = 400 } = {}) {
  let width = 0;
  let height = 0;
  const drawImage = vi.fn((img, _x, _y, w, h) => {
    width = w;
    height = h;
    stub.lastDraw = { img, width: w, height: h };
  });
  const stub = {
    lastDraw: null,
    lastEncode: null,
    get width() {
      return width;
    },
    set width(v) {
      width = v;
    },
    get height() {
      return height;
    },
    set height(v) {
      height = v;
    },
    getContext: () => ({
      drawImage,
      getImageData: () =>
        transparent ? transparentImageData(width, height) : opaqueImageData(width, height),
    }),
    toBlob: (cb, type, quality) => {
      stub.lastEncode = { type, quality };
      const bytes = new Uint8Array(blobSize);
      cb(new Blob([bytes], { type }));
    },
  };
  return stub;
}

describe("resolveAttachmentMaxBytes", () => {
  it("falls back to 8 MB when the config is missing or empty", () => {
    expect(resolveAttachmentMaxBytes(null)).toBe(CHAT_ATTACHMENT_MAX_BYTES_FALLBACK);
    expect(resolveAttachmentMaxBytes({})).toBe(CHAT_ATTACHMENT_MAX_BYTES_FALLBACK);
    expect(resolveAttachmentMaxBytes({ data: {} })).toBe(CHAT_ATTACHMENT_MAX_BYTES_FALLBACK);
  });

  it("reads CHAT_ATTACHMENT_MAX_BYTES from the chat config shapes", () => {
    expect(resolveAttachmentMaxBytes({ CHAT_ATTACHMENT_MAX_BYTES: 16 * 1024 * 1024 })).toBe(
      16 * 1024 * 1024,
    );
    expect(
      resolveAttachmentMaxBytes({ data: { CHAT_ATTACHMENT_MAX_BYTES: 4 * 1024 * 1024 } }),
    ).toBe(4 * 1024 * 1024);
    expect(resolveAttachmentMaxBytes({ data: { chat_attachment_max_bytes: "2097152" } })).toBe(
      2097152,
    );
  });

  it("ignores zero and non-numeric values", () => {
    expect(resolveAttachmentMaxBytes({ CHAT_ATTACHMENT_MAX_BYTES: 0 })).toBe(
      CHAT_ATTACHMENT_MAX_BYTES_FALLBACK,
    );
    expect(resolveAttachmentMaxBytes({ CHAT_ATTACHMENT_MAX_BYTES: "nope" })).toBe(
      CHAT_ATTACHMENT_MAX_BYTES_FALLBACK,
    );
  });
});

describe("attachmentExceedsLimit", () => {
  it("refuses a payload over the limit and accepts one at the limit", () => {
    expect(attachmentExceedsLimit(8 * 1024 * 1024 + 1)).toBe(true);
    expect(attachmentExceedsLimit(8 * 1024 * 1024)).toBe(false);
    expect(attachmentExceedsLimit(100, 99)).toBe(true);
    expect(attachmentExceedsLimit(100, 100)).toBe(false);
  });

  it("uses the 8 MB fallback when maxBytes is missing", () => {
    expect(attachmentExceedsLimit(9 * 1024 * 1024, undefined)).toBe(true);
    expect(attachmentExceedsLimit(100, 0)).toBe(false);
  });
});

describe("formatAttachmentSize and refuse message", () => {
  it("formats B, KB and MB", () => {
    expect(formatAttachmentSize(512)).toBe("512 B");
    expect(formatAttachmentSize(1536)).toBe("1.5 KB");
    expect(formatAttachmentSize(20 * 1024)).toBe("20 KB");
    expect(formatAttachmentSize(2.5 * 1024 * 1024)).toBe("2.5 MB");
  });

  it("names both sizes in the one-line refuse copy", () => {
    expect(refuseAttachmentMessage(9 * 1024 * 1024, 8 * 1024 * 1024)).toBe(
      "This image is 9.0 MB after resize; the limit is 8.0 MB. Choose a smaller image.",
    );
  });
});

describe("chatErrorMessage", () => {
  it("prefers error, then nested message, then message", () => {
    expect(chatErrorMessage({ error: "too big" })).toBe("too big");
    expect(chatErrorMessage({ error: { message: "nested" } })).toBe("nested");
    expect(chatErrorMessage({ message: "fallback" })).toBe("fallback");
    expect(chatErrorMessage(null)).toBe("Chat error");
  });
});

describe("downscaleChatAttachment", () => {
  it("scales the longest edge to 2048 and encodes JPEG at 0.9 for a photo", async () => {
    const canvas = stubCanvas({ blobSize: 320 });
    const result = await downscaleChatAttachment(
      new File([new Uint8Array(8)], "phone.jpg", { type: "image/jpeg" }),
      {
        loadImage: async () => ({ width: 4000, height: 2000 }),
        createCanvas: (w, h) => {
          canvas.width = w;
          canvas.height = h;
          return canvas;
        },
      },
    );
    expect(result.width).toBe(CHAT_ATTACHMENT_MAX_EDGE);
    expect(result.height).toBe(1024);
    expect(canvas.lastEncode).toEqual({
      type: "image/jpeg",
      quality: CHAT_ATTACHMENT_JPEG_QUALITY,
    });
    expect(result.mimeType).toBe("image/jpeg");
    expect(result.byteLength).toBe(320);
    expect(result.file.name).toBe("phone.jpg");
    expect(result.preview.startsWith("data:image/jpeg")).toBe(true);
  });

  it("does not upscale a small image", async () => {
    const canvas = stubCanvas();
    const result = await downscaleChatAttachment(
      new File([new Uint8Array(8)], "tiny.png", { type: "image/png" }),
      {
        loadImage: async () => ({ width: 100, height: 80 }),
        createCanvas: (w, h) => {
          canvas.width = w;
          canvas.height = h;
          return canvas;
        },
      },
    );
    expect(result.width).toBe(100);
    expect(result.height).toBe(80);
  });

  it("keeps PNG only when the source has transparency", async () => {
    const png = stubCanvas({ transparent: true, blobSize: 80 });
    const pngResult = await downscaleChatAttachment(
      new File([new Uint8Array(8)], "logo.png", { type: "image/png" }),
      {
        loadImage: async () => ({ width: 64, height: 64 }),
        createCanvas: (w, h) => {
          png.width = w;
          png.height = h;
          return png;
        },
      },
    );
    expect(png.lastEncode.type).toBe("image/png");
    expect(pngResult.mimeType).toBe("image/png");
    expect(pngResult.file.name).toBe("logo.png");

    const jpegCanvas = stubCanvas({ transparent: false, blobSize: 80 });
    const jpegResult = await downscaleChatAttachment(
      new File([new Uint8Array(8)], "photo.png", { type: "image/png" }),
      {
        loadImage: async () => ({ width: 64, height: 64 }),
        createCanvas: (w, h) => {
          jpegCanvas.width = w;
          jpegCanvas.height = h;
          return jpegCanvas;
        },
      },
    );
    expect(jpegCanvas.lastEncode).toEqual({
      type: "image/jpeg",
      quality: CHAT_ATTACHMENT_JPEG_QUALITY,
    });
    expect(jpegResult.mimeType).toBe("image/jpeg");
    expect(jpegResult.file.name).toBe("photo.jpg");
  });
});

describe("fetchAttachmentMaxBytes", () => {
  beforeEach(() => {
    resetAttachmentMaxBytesCache();
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    resetAttachmentMaxBytesCache();
  });

  it("returns the config value when the chat config answers", async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, data: { CHAT_ATTACHMENT_MAX_BYTES: 16777216 } }),
    });
    await expect(fetchAttachmentMaxBytes()).resolves.toBe(16777216);
  });

  it("falls back to 8 MB when the config route is missing", async () => {
    fetch.mockResolvedValue({ ok: false, status: 404 });
    await expect(fetchAttachmentMaxBytes()).resolves.toBe(CHAT_ATTACHMENT_MAX_BYTES_FALLBACK);
  });
});
