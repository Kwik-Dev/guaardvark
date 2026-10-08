import React, { useRef } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const { uploadFile } = vi.hoisted(() => ({ uploadFile: vi.fn() }));

vi.mock("../../../api", () => ({ uploadFile }));
vi.mock("../../voice/GlobalMicButton", () => ({ default: () => null }));
vi.mock("../SlashCommandPopup", () => ({ default: () => null }));
vi.mock("../../../hooks/useSlashCommands", () => ({
  default: () => ({
    filteredCommands: [],
    selectedIndex: 0,
    selectCommand: () => {},
    popupVisible: false,
    handleInputChange: () => {},
    handleKeyDown: () => {},
    isCommand: false,
    executeCommand: async () => ({ handled: false }),
  }),
}));
vi.mock("../../../contexts/VoiceSessionContext", () => ({
  useVoiceSession: () => ({ toggleHandsFree: () => {} }),
  useVoiceSessionState: (select) => select({ session: null, push: null, phase: null }),
}));
vi.mock("../../../stores/useAppStore", () => ({
  useAppStore: (select) => select({ sessionModes: {}, setSessionMode: () => {} }),
}));
vi.mock("../../../utils/chatAttachment", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchAttachmentMaxBytes: async () => 8 * 1024 * 1024,
  downscaleChatAttachment: async (file) => ({
    file,
    preview: "data:image/png;base64,AAAA",
    byteLength: 4,
    mimeType: "image/png",
  }),
}));

import ChatInput from "../ChatInput";
import useFileDropZone from "../../../hooks/useFileDropZone";

// The Chat page's wiring: the whole chat is the drop zone, the composer takes the files.
function ChatSurface({ onSendMessage, disabled = false }) {
  const inputRef = useRef(null);
  const drop = useFileDropZone({ onFiles: (files) => inputRef.current?.addFiles(files) });
  return (
    <div data-testid="chat" {...drop.dropProps}>
      <div data-testid="message-list">earlier messages</div>
      <ChatInput ref={inputRef} onSendMessage={onSendMessage} sessionId="s1" disabled={disabled} />
    </div>
  );
}

const pdf = () => new File(["%PDF-1.4"], "report.pdf", { type: "application/pdf" });
const png = (name = "photo.png") => new File(["png"], name, { type: "image/png" });
const drop = (el, files) => fireEvent.drop(el, { dataTransfer: { files, types: ["Files"] } });

beforeEach(() => {
  uploadFile.mockReset();
  uploadFile.mockResolvedValue({ document_id: 42 });
  global.fetch = vi.fn(async (url) => {
    if (String(url).startsWith("/api/docs/42")) {
      return { ok: true, json: async () => ({ index_status: "INDEXED" }) };
    }
    return { ok: false, json: async () => ({}) };
  });
});

describe("Chat page drop", () => {
  it("uploads a PDF dropped on the message list the same way as the paperclip", async () => {
    const viaDrop = vi.fn();
    const { unmount } = render(<ChatSurface onSendMessage={viaDrop} />);
    const dropped = pdf();
    expect(drop(screen.getByTestId("message-list"), [dropped])).toBe(false);
    await waitFor(() => expect(viaDrop).toHaveBeenCalledTimes(1));
    const dropUpload = uploadFile.mock.calls[0];
    const dropNotice = viaDrop.mock.calls[0];
    unmount();

    uploadFile.mockClear();
    const viaClip = vi.fn();
    const { container } = render(<ChatSurface onSendMessage={viaClip} />);
    const picked = pdf();
    fireEvent.change(container.querySelector('input[type="file"]'), { target: { files: [picked] } });
    await waitFor(() => expect(viaClip).toHaveBeenCalledTimes(1));
    const clipUpload = uploadFile.mock.calls[0];

    expect(dropUpload[0]).toBe(dropped);
    expect(dropUpload.slice(1, 5)).toEqual([null, "chat-upload,file-upload,s1", {}, null]);
    expect(clipUpload.slice(1, 5)).toEqual(dropUpload.slice(1, 5));
    expect(dropNotice[0]).toContain("**Document Uploaded Successfully**");
    expect(dropNotice[0]).toContain("report.pdf");
    expect(dropNotice[0]).toContain("**Status:** Uploaded and indexed");
    expect(viaClip.mock.calls[0]).toEqual(dropNotice);
  });

  it("uploads a text file dropped on the composer", async () => {
    const send = vi.fn();
    render(<ChatSurface onSendMessage={send} />);
    const notes = new File(["hello"], "notes.txt", { type: "text/plain" });
    expect(drop(screen.getByPlaceholderText(/Type your message/), [notes])).toBe(false);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(uploadFile.mock.calls[0][0]).toBe(notes);
    expect(send.mock.calls[0][0]).toContain("notes.txt");
  });

  it("holds the notice until the reply in progress has finished", async () => {
    const send = vi.fn();
    const { rerender } = render(<ChatSurface onSendMessage={send} disabled />);
    drop(screen.getByTestId("message-list"), [pdf()]);
    await waitFor(() => expect(uploadFile).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(send).not.toHaveBeenCalled();
    rerender(<ChatSurface onSendMessage={send} />);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
  });

  it("attaches a dropped image like a paste and sends no upload", async () => {
    const send = vi.fn();
    render(<ChatSurface onSendMessage={send} />);
    drop(screen.getByTestId("message-list"), [png()]);
    expect(await screen.findByAltText("photo.png")).toBeInTheDocument();
    expect(uploadFile).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
    expect(screen.queryByText(/Only the first image is sent/)).not.toBeInTheDocument();
  });

  it("says so when more than one image is attached, and sends only the first", async () => {
    const send = vi.fn();
    render(<ChatSurface onSendMessage={send} />);
    drop(screen.getByTestId("message-list"), [png("a.png"), png("b.png")]);
    expect(await screen.findByAltText("b.png")).toBeInTheDocument();
    expect(screen.getByText(/Only the first image is sent to the model/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Analyze image" }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const [, , options] = send.mock.calls[0];
    expect(options.imageFileName).toBe("a.png");
  });
});
