// frontend/src/components/chat/ChatInput.jsx
// Version 2.0: Unified API service integration
import AttachFileIcon from "@mui/icons-material/AttachFile";
import CloseIcon from "@mui/icons-material/Close";
import SendIcon from "@mui/icons-material/Send";
import StopIcon from "@mui/icons-material/Stop";
import {
  Alert,
  Box,
  Card,
  CardMedia,
  Chip,
  IconButton,
  LinearProgress,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import React, {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";

import GlobalMicButton from "../voice/GlobalMicButton";
import { useAppStore } from "../../stores/useAppStore";
import { useVoiceSession, useVoiceSessionState } from "../../contexts/VoiceSessionContext";
import useSlashCommands from "../../hooks/useSlashCommands";
import SlashCommandPopup from "./SlashCommandPopup";
import { debugLog } from "../../utils/debugLog";
import { StatusPill } from "../settings/ui";
import {
  attachmentExceedsLimit,
  downscaleChatAttachment,
  fetchAttachmentMaxBytes,
  formatAttachmentSize,
  refuseAttachmentMessage,
} from "../../utils/chatAttachment";
import {
  CHAT_ATTACH_ACCEPT,
  chatDocumentRefusal,
  splitChatFiles,
  uploadChatDocument,
} from "../../utils/chatDocumentUpload";

const ChatInput = forwardRef(
  ({ onSendMessage, onStop, disabled = false, chimeIn = false, onChimeIn, sessionId = "default", codeGenMode = false, onVoiceStateChange = () => { }, onAddMessage, onUpdateMessage, onClearMessages, onPlanCreated, projectId, composerError, onClearComposerError }, ref) => {
    const [inputText, setInputText] = useState("");
    const fileRef = useRef(null);
    const inputRef = useRef(null);

    // Terminal-style sent-message history. Up/Down navigate when the cursor
    // is at the very start/end of the input and the slash-command popup
    // hasn't already consumed the key.
    const messageHistoryRef = useRef([]);
    const historyIndexRef = useRef(-1);
    const historyDraftRef = useRef("");
    const HISTORY_MAX = 50;

    const pushHistory = useCallback((text) => {
      const trimmed = (text || "").trim();
      if (!trimmed) return;
      const hist = messageHistoryRef.current;
      if (hist[hist.length - 1] !== trimmed) {
        hist.push(trimmed);
        if (hist.length > HISTORY_MAX) hist.shift();
      }
      historyIndexRef.current = -1;
      historyDraftRef.current = "";
    }, []);

    const recallHistory = useCallback((direction) => {
      const hist = messageHistoryRef.current;
      if (hist.length === 0) return false;
      const el = inputRef.current;
      if (!el) return false;
      const value = el.value ?? "";
      const atStart = el.selectionStart === 0 && el.selectionEnd === 0;
      const atEnd =
        el.selectionStart === value.length &&
        el.selectionEnd === value.length;

      if (direction === "up") {
        if (!atStart) return false;
        if (historyIndexRef.current === -1) {
          historyDraftRef.current = value;
          historyIndexRef.current = hist.length - 1;
        } else if (historyIndexRef.current > 0) {
          historyIndexRef.current -= 1;
        } else {
          return true; // already oldest — consume so cursor doesn't jump
        }
        const next = hist[historyIndexRef.current];
        setInputText(next);
        // Move cursor to end so the user can edit
        requestAnimationFrame(() => {
          if (inputRef.current) {
            const len = next.length;
            inputRef.current.setSelectionRange(len, len);
          }
        });
        return true;
      }
      // direction === "down"
      if (historyIndexRef.current === -1) return false;
      if (!atEnd) return false;
      if (historyIndexRef.current < hist.length - 1) {
        historyIndexRef.current += 1;
        const next = hist[historyIndexRef.current];
        setInputText(next);
        requestAnimationFrame(() => {
          if (inputRef.current) {
            const len = next.length;
            inputRef.current.setSelectionRange(len, len);
          }
        });
      } else {
        historyIndexRef.current = -1;
        const draft = historyDraftRef.current;
        setInputText(draft);
        requestAnimationFrame(() => {
          if (inputRef.current) {
            const len = draft.length;
            inputRef.current.setSelectionRange(len, len);
          }
        });
      }
      return true;
    }, []);

    // The global voice session: the mic here is the same one as in the top bar.
    const voiceSession = useVoiceSession();
    const voiceListening = useVoiceSessionState((s) => Boolean(s.session || s.push));
    const voiceCapturing = useVoiceSessionState((s) => s.phase === "capturing");

    // Modal session mode — "chat" | "agent". The session's mode is stored
    // server-side; we cache it in Zustand and hydrate on session change.
    // When in agent mode, a non-slash send goes straight to the agent loop
    // instead of the chat LLM.
    const sessionMode = useAppStore((s) => s.sessionModes[sessionId] || "chat");
    const setSessionMode = useAppStore((s) => s.setSessionMode);
    const agentModeActive = sessionMode === "agent";

    useEffect(() => {
      if (!sessionId) return;
      let cancelled = false;
      (async () => {
        try {
          const res = await fetch(
            `/api/chat-sessions/${encodeURIComponent(sessionId)}/mode`
          );
          if (!res.ok) return;
          const data = await res.json();
          if (!cancelled && data?.success && data.mode) {
            setSessionMode(sessionId, data.mode);
          }
        } catch {
          // Network failure → leave the cached value (or "chat" default).
        }
      })();
      return () => { cancelled = true; };
    }, [sessionId, setSessionMode]);

    // Slash command hook — popup state, filtering, keyboard nav, command execution
    const slashCmds = useSlashCommands({
      inputRef,
      addMessage: onAddMessage || ((msg) => onSendMessage?.(msg.content, null)),
      updateMessage: onUpdateMessage || (() => {}),
      onSendMessage,
      setInputText,
      chatState: {
        sessionId,
        projectId,
        clearMessages: onClearMessages,
        onPlanCreated,
        voiceContext: { toggleVoice: voiceSession.toggleHandsFree, isVoiceActive: voiceListening },
      },
    });

    // Voice state for the page's background waveform.
    useEffect(() => {
      onVoiceStateChange({
        isListening: voiceListening,
        isUserSpeaking: voiceCapturing,
        audioLevels: [],
      });
    }, [voiceListening, voiceCapturing, onVoiceStateChange]);

    // Show initial mode status on component mount only
    useEffect(() => {
      if (window.showMessage) {
        setTimeout(() => {
          // Show initial status for Universal RAG (no specific mode active)
          window.showMessage(
            `Chat interface ready - **UNIVERSAL RAG** active (all data accessible, no mode restrictions)`,
            "info"
          );
        }, 1000); // Delay to ensure other startup messages are shown first
      }
    }, []); // Only run on mount

    // File upload state
    const [fileUploadState, setFileUploadState] = useState({
      uploading: false,
      progress: 0,
      fileName: null,
      error: null,
    });

    // Image upload and paste state (supports multiple images).
    // Each entry is already downscaled (see handleImageUpload); byteLength is
    // the payload that will ride on chat:send.
    const MAX_IMAGES = 4;
    const [imageState, setImageState] = useState({
      images: [],        // Array of { file, preview, id, byteLength, mimeType }
      analyzing: false,
      error: null,
    });
    const [attachmentMaxBytes, setAttachmentMaxBytes] = useState(null);

    useEffect(() => {
      let cancelled = false;
      fetchAttachmentMaxBytes().then((max) => {
        if (!cancelled) setAttachmentMaxBytes(max);
      });
      return () => { cancelled = true; };
    }, []);
    // Backwards-compatible getters for single-image code paths
    const _selectedImage = imageState.images.length > 0 ? imageState.images[0].file : null;
    const _imagePreview = imageState.images.length > 0 ? imageState.images[0].preview : null;

    // A document notice waits here while a reply is streaming: the page drops
    // messages sent while it is busy.
    const [pendingNotices, setPendingNotices] = useState([]);
    useEffect(() => {
      if (disabled || pendingNotices.length === 0) return;
      onSendMessage(pendingNotices.join("\n\n"), null);
      setPendingNotices([]);
    }, [disabled, pendingNotices, onSendMessage]);

    // Documents from the paperclip or a drop: uploaded one after another, then
    // one notice for all of them.
    const handleDocumentFiles = async (files) => {
      const notices = [];
      for (const file of files) {
        debugLog("Starting file upload", { fileName: file.name, size: file.size });
        const refusal = chatDocumentRefusal(file);
        if (refusal) {
          notices.push(refusal);
          continue;
        }
        setFileUploadState({ uploading: true, progress: 0, fileName: file.name, error: null });
        const result = await uploadChatDocument(file, {
          sessionId,
          codeGenMode,
          onStage: ({ progress, indexing }) =>
            setFileUploadState((prev) => ({
              ...prev,
              progress,
              fileName: indexing ? `${file.name} (indexing...)` : file.name,
            })),
        });
        if (!result.ok) {
          setFileUploadState({ uploading: false, progress: 0, fileName: null, error: result.error });
        }
        notices.push(result.message);
      }
      if (fileRef.current) fileRef.current.value = "";
      setFileUploadState((prev) => (prev.error ? prev : { uploading: false, progress: 0, fileName: null, error: null }));
      if (notices.length) setPendingNotices((prev) => [...prev, ...notices]);
    };

    // Everything the paperclip, a paste or a drop hands the composer.
    const addFiles = (files) => {
      const { images, documents } = splitChatFiles(files);
      images.forEach((img) => handleImageUpload(img));
      if (documents.length) handleDocumentFiles(documents);
    };

    useImperativeHandle(ref, () => ({
      focus: () => {
        inputRef.current?.focus();
      },
      addFiles,
    }));

    const handleFileSelect = (event) => {
      const file = event.target.files?.[0];
      if (!file) return;
      debugLog("File selected", { fileName: file.name, size: file.size });
      // Cleared now: left in the input, the send path would read it back as a
      // document for the next message and open the upload dialog.
      event.target.value = "";
      addFiles([file]);
    };

    // Image handling functions — downscale through a canvas before the
    // preview is stored, so chat:send never carries a raw phone photo.
    const handleImageUpload = async (file) => {
      if (!file) return;

      if (!file.type.startsWith("image/")) {
        setImageState((prev) => ({
          ...prev,
          error: "Please select an image file",
        }));
        return;
      }

      try {
        const resized = await downscaleChatAttachment(file);
        setImageState((prev) => {
          if (prev.images.length >= MAX_IMAGES) {
            return { ...prev, error: `Maximum ${MAX_IMAGES} images allowed` };
          }
          return {
            ...prev,
            images: [
              ...prev.images,
              {
                file: resized.file,
                preview: resized.preview,
                byteLength: resized.byteLength,
                mimeType: resized.mimeType,
                id: `img_${Date.now()}_${Math.random().toString(36).substr(2, 5)}`,
              },
            ],
            error: null,
          };
        });
      } catch (err) {
        setImageState((prev) => ({
          ...prev,
          error: err?.message || "Could not prepare this image",
        }));
      }
    };

    const handleImagePaste = (event) => {
      const items = event.clipboardData?.items;
      if (!items) return;

      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.type.startsWith("image/")) {
          const file = item.getAsFile();
          if (file) {
            handleImageUpload(file);
            event.preventDefault();
          }
          break;
        }
      }
    };

    const clearImage = (imageId) => {
      if (imageId) {
        // Remove a specific image
        setImageState((prev) => ({
          ...prev,
          images: prev.images.filter((img) => img.id !== imageId),
          error: null,
        }));
      } else {
        // Clear all images
        setImageState({ images: [], analyzing: false, error: null });
      }
    };

    const analyzeImage = async () => {
      if (imageState.images.length === 0) return;

      const primaryImage = imageState.images[0];
      const maxBytes = attachmentMaxBytes;
      const over = imageState.images.find((img) =>
        attachmentExceedsLimit(img.byteLength, maxBytes),
      );
      if (over) {
        setImageState((prev) => ({
          ...prev,
          error: refuseAttachmentMessage(over.byteLength, maxBytes),
        }));
        return;
      }

      const useUnified = localStorage.getItem("use_unified_chat") !== "false";

      setImageState((prev) => ({ ...prev, analyzing: true, error: null }));

      try {
        if (useUnified) {
          // Unified chat path: send base64 image through the ReACT loop
          const base64 = primaryImage.preview.split(",")[1];
          const messageText =
            inputText || `Describe this image: ${primaryImage.file.name}`;
          onSendMessage(messageText, null, {
            isImageAnalysis: true,
            imageBase64: base64,
            imageFileName: primaryImage.file.name,
            imagePreview: primaryImage.preview,
          });

          clearImage();
          setInputText("");
        } else {
          // Legacy vision endpoint path (single image only)
          const formData = new FormData();
          formData.append("image", primaryImage.file);
          formData.append("session_id", sessionId);
          formData.append("message", inputText);

          const response = await fetch("/api/enhanced-chat/vision/analyze", {
            method: "POST",
            body: formData,
          });

          if (!response.ok) {
            throw new Error(`Analysis failed: ${response.status}`);
          }

          const result = await response.json();

          if (result.success) {
            onSendMessage("", null, {
              isImageAnalysis: true,
              imageFileName: primaryImage.file.name,
              analysisResponse: result.response,
              analysisDetails: result.analysis_details,
              imageUrl: result.image_url,
              permanentFileName: result.image_filename,
            });

            clearImage();
            setInputText("");
          } else {
            throw new Error(result.error || "Analysis failed");
          }
        }
      } catch (error) {
        console.error("Image analysis error:", error);

        const errorMessage = `**Vision Analysis Failed**

**Image:** ${primaryImage.file.name}
**Error:** ${error.message}

Please try a different image or check if the vision model is properly loaded.`;
        onSendMessage(errorMessage, null);

        clearImage();
        setInputText("");
      } finally {
        setImageState((prev) => ({ ...prev, analyzing: false }));
      }
    };

    // Add paste event listener
    useEffect(() => {
      const handlePasteEvent = (event) => {
        // Only handle paste if the input is focused or if we're in the chat area
        const activeElement = document.activeElement;
        const isInputFocused = activeElement === inputRef.current;
        const isChatArea =
          activeElement?.closest(".chat-container") || !activeElement;

        if (isInputFocused || isChatArea) {
          handleImagePaste(event);
        }
      };

      document.addEventListener("paste", handlePasteEvent);
      return () => {
        document.removeEventListener("paste", handlePasteEvent);
      };
    }, []);

    // While the screen agent is working, the box stays open and what is
    // typed goes to the running task as a note instead of a new message.
    const noteMode = disabled && chimeIn && typeof onChimeIn === "function";

    // Esc presses Stop while a reply is running, but only when Stop could be
    // clicked. Whether something covers the button is read in the capture
    // phase, before an image viewer's own Esc handler can close it; the press
    // is acted on in the bubble phase, after a dialog, the slash popup or the
    // agent-screen key forwarder has had the chance to take the key.
    const rootRef = useRef(null);
    const stopButtonRef = useRef(null);
    useEffect(() => {
      if (!disabled || typeof onStop !== "function") return undefined;
      let stopReachable = false;
      const stopIsTopmost = () => {
        const btn = stopButtonRef.current;
        if (!btn) return false;
        const r = btn.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return !!hit && btn.contains(hit);
      };
      const onCapture = (e) => {
        stopReachable = e.key === "Escape" && stopIsTopmost();
      };
      const onBubble = (e) => {
        if (e.key !== "Escape" || !stopReachable) return;
        stopReachable = false;
        if (e.defaultPrevented || e.repeat || e.isComposing) return;
        const t = e.target;
        const editable = t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));
        if (editable && !rootRef.current?.contains(t)) return;
        e.preventDefault();
        onStop();
      };
      window.addEventListener("keydown", onCapture, true);
      window.addEventListener("keydown", onBubble);
      return () => {
        window.removeEventListener("keydown", onCapture, true);
        window.removeEventListener("keydown", onBubble);
      };
    }, [disabled, onStop]);

    const handleSend = async () => {
      // Capture what the user typed for terminal-style history before any
      // branch consumes/clears it.
      pushHistory(inputText);

      if (noteMode) {
        const note = (inputText || inputRef.current?.value || "").trim();
        if (!note) return;
        onChimeIn(note);
        setInputText("");
        if (inputRef.current) {
          inputRef.current.value = "";
          inputRef.current.focus();
        }
        return;
      }

      // Check if there are images to analyze
      if (imageState.images.length > 0) {
        await analyzeImage();
        return;
      }

      // Fallback: If programmatic input bypassed React state, grab from DOM
      let currentText = inputText;
      if (!currentText && inputRef.current && inputRef.current.value) {
        currentText = inputRef.current.value;
      }

      // Slash commands run through their registry handlers before any other
      // logic. The text is checked as well as isCommand, which only follows
      // typing: a command recalled from history (Up arrow) must run the same way.
      if (slashCmds.isCommand || currentText.trim().startsWith("/")) {
        const result = await slashCmds.executeCommand(currentText);
        if (result?.handled) {
          setInputText("");
          if (inputRef.current) {
            inputRef.current.value = "";
            inputRef.current.focus();
          }
          return;
        }
      }

      const file = fileRef.current?.files?.[0] || null;
      if (!currentText.trim() && !file) return;

      // Agent mode: messages flow through the normal chat pipeline so the
      // LLM can both speak AND act. The session's `mode === "agent"` flag
      // makes unifiedChatService flip `agent_screen_active: true`, which
      // activates the Gemma4 direct path and exposes the screen tools.
      // The orange chip above the input + the user bubble's `mode: "agent"`
      // marker are the visual signal that we're in agent mode.

      // Input validation and sanitization
      const sanitizedInput = currentText.trim();
      const maxLength = 100000; // 100k character limit for file analysis

      if (sanitizedInput.length > maxLength) {
        onSendMessage(
          `Message too long. Please limit to ${maxLength} characters. Current length: ${sanitizedInput.length}`,
          null
        );
        return;
      }

      // Pass intelligent mode to parent (automatic mode selection)
      onSendMessage(currentText, file, { chatMode: "analyze" });
      setInputText("");
      if (fileRef.current) fileRef.current.value = "";
      if (inputRef.current) {
        inputRef.current.value = "";
        inputRef.current.focus();
      }
    };

    const handleKeyPress = (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    };

    return (
      <Box
        ref={rootRef}
        sx={{
          p: 2,
          borderTop: 1,
          borderColor: "divider",
          display: "flex",
          flexDirection: "column",
          gap: 1,
        }}
      >
        {fileUploadState.uploading && (
          <Box data-testid="chat-upload-progress">
            <Typography variant="caption" color="text.secondary">
              Uploading {fileUploadState.fileName}
            </Typography>
            <LinearProgress
              variant="determinate"
              value={Math.min(100, Math.max(0, fileUploadState.progress || 0))}
              sx={{ height: 4, borderRadius: 2 }}
            />
          </Box>
        )}

        {/* File upload error */}
        {fileUploadState.error && (
          <Alert
            severity="error"
            sx={{ mb: 1 }}
            onClose={() =>
              setFileUploadState((prev) => ({ ...prev, error: null }))
            }
          >
            Upload failed: {fileUploadState.error}
          </Alert>
        )}

        {/* Image preview thumbnails */}
        {imageState.images.length > 0 && (
          <Card sx={{ mb: 1, p: 1.5 }}>
            <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap", alignItems: "center" }}>
              {imageState.images.map((img) => (
                <Box key={img.id} sx={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 0.5 }}>
                  <Box sx={{ position: "relative", width: 80, height: 80 }}>
                    <CardMedia
                      component="img"
                      sx={{
                        width: 80,
                        height: 80,
                        objectFit: "cover",
                        borderRadius: 1,
                        border: "1px solid #e0e0e0",
                      }}
                      image={img.preview}
                      alt={img.file.name}
                    />
                    <IconButton
                      size="small"
                      onClick={() => clearImage(img.id)}
                      disabled={imageState.analyzing}
                      sx={{
                        position: "absolute",
                        top: -8,
                        right: -8,
                        bgcolor: "background.paper",
                        border: "1px solid",
                        borderColor: "divider",
                        width: 20,
                        height: 20,
                        "&:hover": { bgcolor: "error.light", color: "white" },
                      }}
                    >
                      <CloseIcon sx={{ fontSize: 12 }} />
                    </IconButton>
                  </Box>
                  <StatusPill
                    label={formatAttachmentSize(img.byteLength)}
                    tone={attachmentExceedsLimit(img.byteLength, attachmentMaxBytes) ? "error" : "neutral"}
                    tooltip={
                      attachmentExceedsLimit(img.byteLength, attachmentMaxBytes)
                        ? refuseAttachmentMessage(img.byteLength, attachmentMaxBytes)
                        : "Size after resize, as it will be sent"
                    }
                  />
                </Box>
              ))}
            </Box>
            {imageState.images.length > 1 && (
              <Alert severity="info" sx={{ mt: 1, py: 0 }}>
                Only the first image is sent to the model. Remove it to send another one.
              </Alert>
            )}
            <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mt: 1 }}>
              <Chip
                icon={<AttachFileIcon />}
                label={`${imageState.images.length} image${imageState.images.length > 1 ? "s" : ""} selected`}
                size="small"
                variant="outlined"
              />
              <IconButton
                size="small"
                onClick={() => clearImage()}
                disabled={imageState.analyzing}
              >
                <CloseIcon />
              </IconButton>
            </Box>

            {imageState.analyzing && (
              <Box sx={{ mt: 1 }}>
                <Box
                  sx={{
                    color: "text.secondary",
                    fontSize: "0.875rem",
                    mt: 0.5,
                  }}
                >
                  Analyzing image...
                </Box>
              </Box>
            )}
          </Card>
        )}

        {/* Image error */}
        {imageState.error && (
          <Alert
            severity="error"
            sx={{ mb: 1 }}
            onClose={() => setImageState((prev) => ({ ...prev, error: null }))}
          >
            {imageState.error}
          </Alert>
        )}

        {composerError && (
          <Alert
            severity="error"
            sx={{ mb: 1, py: 0 }}
            onClose={() => onClearComposerError?.()}
          >
            {composerError}
          </Alert>
        )}

        <Box sx={{ display: "flex", gap: 1, alignItems: "flex-end" }}>
          <input
            type="file"
            hidden
            ref={fileRef}
            onChange={handleFileSelect}
            accept={CHAT_ATTACH_ACCEPT}
          />

          {/* File attachment button */}
          <Tooltip title="Attach file or image">
            {disabled ? (
              <span>
                <IconButton
                  onClick={() => fileRef.current?.click()}
                  disabled={disabled}
                  sx={{ color: "text.secondary" }}
                >
                  <AttachFileIcon />
                </IconButton>
              </span>
            ) : (
              <IconButton
                onClick={() => fileRef.current?.click()}
                disabled={disabled}
                sx={{ color: "text.secondary" }}
              >
                <AttachFileIcon />
              </IconButton>
            )}
          </Tooltip>

          {/* The global mic: hold to talk, click per the activation mode. */}
          <Box sx={{ display: "flex", alignItems: "center", alignSelf: "center" }}>
            <GlobalMicButton variant="inline" label="Voice" showMenu={false} />
          </Box>

          {/* Slash command autocomplete popup */}
          <SlashCommandPopup
            commands={slashCmds.filteredCommands}
            selectedIndex={slashCmds.selectedIndex}
            onSelect={slashCmds.selectCommand}
            anchorEl={inputRef?.current}
            open={slashCmds.popupVisible}
          />

          {/* Agent mode badge — sits above the input when active */}
          {agentModeActive && (
            <Chip
              label="AGENT MODE — type /chat to exit"
              color="warning"
              size="small"
              sx={{
                position: "absolute",
                top: -28,
                left: 8,
                fontWeight: 600,
                letterSpacing: 0.5,
                zIndex: 2,
              }}
            />
          )}

          {/* Text input field */}
          <TextField
            fullWidth
            size="small"
            placeholder={
              noteMode
                ? "Add a note for the agent — it reads it at its next step and keeps going"
                : agentModeActive
                ? "Describe a screen action — every message is a task while in agent mode"
                : imageState.images.length > 0
                  ? "Ask about this image..."
                  : "Type your message, paste an image, or use voice..."
            }
            value={inputText}
            onChange={(e) => {
              setInputText(e.target.value);
              slashCmds.handleInputChange(e.target.value);
            }}
            onKeyDown={(e) => {
              slashCmds.handleKeyDown(e);
              if (e.defaultPrevented) return;
              if (e.key === "ArrowUp" && recallHistory("up")) {
                e.preventDefault();
              } else if (e.key === "ArrowDown" && recallHistory("down")) {
                e.preventDefault();
              }
              // handleKeyPress uses onKeyPress but we mirror Enter logic here for safety
            }}
            onKeyPress={handleKeyPress}
            inputRef={inputRef}
            multiline
            disabled={(disabled && !noteMode) || imageState.analyzing}
            sx={{ 
              minHeight: "40px",
              ...(agentModeActive && {
                '& .MuiOutlinedInput-root': {
                  '& fieldset': {
                    borderColor: 'warning.main',
                    borderWidth: 2,
                  },
                  '&:hover fieldset': {
                    borderColor: 'warning.main',
                  },
                  '&.Mui-focused fieldset': {
                    borderColor: 'warning.main',
                  },
                }
              })
            }}
          />

          {/* Send-note button, beside Stop, while the agent is working */}
          {noteMode && (
            <Tooltip title="Send note to the agent (it keeps working)">
              <span>
                <IconButton
                  color="warning"
                  onClick={handleSend}
                  disabled={!inputText.trim()}
                >
                  <SendIcon />
                </IconButton>
              </span>
            </Tooltip>
          )}

          {/* Send button */}
          <Tooltip
            title={
              disabled
                ? "Stop (Esc)"
                : imageState.analyzing
                  ? "Analyzing image..."
                  : imageState.images.length > 0
                    ? "Analyze image"
                    : "Send message"
            }
          >
            {imageState.analyzing ? (
              <span>
                <IconButton
                  ref={stopButtonRef}
                  color="primary"
                  onClick={disabled ? onStop : handleSend}
                  disabled={imageState.analyzing} // Disable during analysis
                >
                  {disabled ? <StopIcon /> : <SendIcon />}
                </IconButton>
              </span>
            ) : (
              <IconButton
                ref={stopButtonRef}
                color="primary"
                onClick={disabled ? onStop : handleSend}
                disabled={imageState.analyzing} // Disable during analysis
              >
                {disabled ? <StopIcon /> : <SendIcon />}
              </IconButton>
            )}
          </Tooltip>
        </Box>
      </Box>
    );
  }
);

ChatInput.displayName = "ChatInput";

export default ChatInput;