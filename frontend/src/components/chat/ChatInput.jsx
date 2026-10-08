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
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import CollapsibleAlert from "../common/CollapsibleAlert";
import React, {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";

import * as apiService from "../../api";
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

    useImperativeHandle(ref, () => ({
      focus: () => {
        inputRef.current?.focus();
      },
    }));

    // Enhanced file upload handler using unified API service
    const handleFileUpload = async (file) => {
      debugLog("Starting file upload", { fileName: file.name, size: file.size });

      setFileUploadState({
        uploading: true,
        progress: 0,
        fileName: file.name,
        error: null,
      });

      try {
        // Add session ID to tags for proper file association
        const _extension = "." + file.name.split(".").pop().toLowerCase();
        const tags = `chat-upload,file-upload,${sessionId}`;

        // Upload file using unified API service
        const result = await apiService.uploadFile(
          file,
          null, // projectId
          tags,
          {},
          null, // signal
          (progressData) => {
            setFileUploadState((prev) => ({
              ...prev,
              progress: progressData.percentage,
            }));
          }
        );

        if (result.error) {
          throw new Error(result.error);
        }

        debugLog("Upload result", {
          success: result?.success,
          documentId: result?.document_id || result?.id,
        });

        // Enhanced file storage: Check if this is a code file
        const fileType = file.name.split(".").pop().toLowerCase();
        const codeFileExtensions = [
          ".js",
          ".jsx",
          ".ts",
          ".tsx",
          ".py",
          ".java",
          ".cpp",
          ".c",
          ".h",
          ".hpp",
          ".cs",
          ".php",
          ".rb",
          ".go",
          ".rs",
          ".swift",
          ".kt",
          ".scala",
          ".sh",
          ".bash",
          ".sql",
          ".css",
          ".scss",
          ".sass",
          ".html",
          ".htm",
          ".xml",
          ".json",
          ".yaml",
          ".yml",
          ".vue",
          ".svelte",
          ".dart",
          ".r",
          ".lua",
        ];
        const isCodeFile = codeFileExtensions.includes("." + fileType);

        setFileUploadState((prev) => ({
          ...prev,
          progress: 75,
          fileName: file.name,
          error: null,
        }));

        // Wait for indexing to complete before sending chat message
        let indexingComplete = false;
        let attempts = 0;
        const maxAttempts = 30; // 30 seconds max wait

        while (!indexingComplete && attempts < maxAttempts) {
          await new Promise((resolve) => setTimeout(resolve, 1000)); // Wait 1 second
          attempts++;

          try {
            // Check document indexing status
            const statusResponse = await fetch(
              `/api/docs/${result.document_id}`
            );
            if (statusResponse.ok) {
              const docData = await statusResponse.json();
              if (
                docData.index_status === "INDEXED" ||
                docData.index_status === "STORED"
              ) {
                indexingComplete = true;
                break;
              } else if (docData.index_status === "ERROR") {
                console.warn("Document indexing failed");
                break;
              }
            }
          } catch (error) {
            console.warn("Error checking indexing status:", error);
          }

          // Update progress to show we're waiting
          setFileUploadState((prev) => ({
            ...prev,
            progress: 75 + (attempts / maxAttempts) * 20,
            fileName: `${file.name} (indexing...)`,
          }));
        }

        setFileUploadState((prev) => ({ ...prev, progress: 100 }));

        // Enhanced success message based on file type and indexing status
        const fileSizeKB = (file.size / 1024).toFixed(1);
        const indexingStatus = indexingComplete
          ? "Uploaded and indexed successfully"
          : "Uploaded (indexing in progress)";

        let uploadMessage;
        if (codeGenMode && isCodeFile && indexingComplete) {
          // For CodeGen mode, send processing request instead of notification
          uploadMessage = `/codegen

Please analyze and refactor the uploaded code file: ${file.name}

Requirements:
- Analyze the complete file content (${fileSizeKB} KB)
- Provide clean, refactored code only (no commentary)
- Maintain all functionality while improving code structure
- Fix any obvious issues or inefficiencies

Document ID: ${result.document_id}`;
        } else if (codeGenMode && !indexingComplete) {
          // If indexing not complete in CodeGen mode, inform user to wait
          uploadMessage = `**File Upload Complete - Indexing in Progress**

Please wait for indexing to complete before processing. File: ${file.name} (${fileSizeKB} KB)`;
        } else if (isCodeFile) {
          // Regular notification for non-CodeGen mode
          uploadMessage = `**Code File Uploaded Successfully**

**File Details:**
- **Name:** ${file.name}
- **Type:** ${fileType.toUpperCase()} (Code File)
- **Size:** ${fileSizeKB} KB
- **Document ID:** ${result.document_id || "N/A"}

**Status:** ${indexingStatus}
**Enhanced Analysis:** Code content is ${indexingComplete ? "now" : "being"
            } indexed and ${indexingComplete ? "available" : "will be available"
            } for search and discussion.

${indexingComplete
              ? "You can ask questions about this code file and I'll analyze the complete content!"
              : "Please wait a moment for indexing to complete, then ask questions about the code."
            }`;
        } else {
          uploadMessage = `**Document Uploaded Successfully**

**File Details:**
- **Name:** ${file.name}
- **Type:** ${fileType.toUpperCase()}
- **Size:** ${fileSizeKB} KB
- **Document ID:** ${result.document_id || "N/A"}

**Status:** ${indexingStatus}
**RAG Integration:** The document is ${indexingComplete ? "now" : "being"
            } indexed and ${indexingComplete ? "available" : "will be available"
            } for search and context retrieval.`;
        }

        // Send message to chat
        onSendMessage(uploadMessage, null);

        // Clear file input
        if (fileRef.current) {
          fileRef.current.value = "";
        }

        debugLog("File upload process completed successfully");
      } catch (error) {
        console.error("File upload failed:", error);

        setFileUploadState({
          uploading: false,
          progress: 0,
          fileName: null,
          error: error.message,
        });

        // Send error message to chat
        const errorMessage = `**File Upload Failed**

**File:** ${file.name}
**Error:** ${error.message}

Please try uploading the file again or contact support if the issue persists.`;

        onSendMessage(errorMessage, null);
      } finally {
        // Reset upload state after delay
        setTimeout(() => {
          setFileUploadState({
            uploading: false,
            progress: 0,
            fileName: null,
            error: null,
          });
        }, 2000);
      }
    };

    // Supported file types for chat analysis
    const supportedFileTypes = {
      // Programming files
      ".py": "Python",
      ".js": "JavaScript",
      ".jsx": "React JSX",
      ".ts": "TypeScript",
      ".tsx": "TypeScript React",
      ".html": "HTML",
      ".css": "CSS",
      ".scss": "SCSS",
      ".json": "JSON",
      ".xml": "XML",
      ".yaml": "YAML",
      ".yml": "YAML",
      ".toml": "TOML",
      ".ini": "INI Config",
      ".conf": "Config",
      ".env": "Environment",

      // Data files
      ".csv": "CSV Data",
      ".xlsx": "Excel",
      ".xls": "Excel",

      // Documents
      ".pdf": "PDF Document",
      ".txt": "Text File",
      ".md": "Markdown",
      ".rst": "ReStructuredText",

      // Other common formats
      ".sql": "SQL",
      ".sh": "Shell Script",
      ".bat": "Batch File",
      ".ps1": "PowerShell",
      ".dockerfile": "Dockerfile",
      ".gitignore": "Git Ignore",
      ".gitattributes": "Git Attributes",
    };

    // File selection handler
    const handleFileSelect = (event) => {
      const file = event.target.files?.[0];
      if (file) {
        debugLog("File selected", { fileName: file.name, size: file.size });

        // Check if it's an image first
        if (file.type.startsWith("image/")) {
          handleImageUpload(file);
          // The image now lives in imageState. Left in the input, the send
          // path would read it back as a document for the next message and
          // open the upload dialog instead of sending that message.
          event.target.value = "";
          return;
        }

        // Validate file size (100MB limit)
        const maxSize = 100 * 1024 * 1024;
        if (file.size > maxSize) {
          const errorMessage = `**File Too Large**

**File:** ${file.name}
**Size:** ${(file.size / 1024 / 1024).toFixed(1)} MB
**Limit:** 100 MB

Please select a smaller file or compress the file before uploading.`;

          onSendMessage(errorMessage, null);
          return;
        }

        // Validate file type
        const extension = "." + file.name.split(".").pop().toLowerCase();
        const isSupported =
          supportedFileTypes[extension] || file.type.startsWith("text/");

        if (!isSupported) {
          const supportedTypes = Object.keys(supportedFileTypes).join(", ");
          const errorMessage = `**Unsupported File Type**

**File:** ${file.name}
**Type:** ${extension}
**Supported Types:** ${supportedTypes}, images

Please select a supported file type.`;

          onSendMessage(errorMessage, null);
          return;
        }

        // Start upload process
        handleFileUpload(file);
      }
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

    // Drag-and-drop: accept LOCAL image files only. We read dataTransfer.FILES and
    // deliberately NEVER read dataTransfer URLs (text/uri-list / text/html) — a drag
    // from a browser tab carries a remote URL there, and fetching it would break
    // offline-first. Local files go through the same base64 path as paste/paperclip.
    const handleImageDrop = (event) => {
      event.preventDefault();
      const files = event.dataTransfer?.files;
      if (!files || !files.length) return;
      for (const f of Array.from(files)) {
        if (f.type && f.type.startsWith("image/")) handleImageUpload(f);
      }
    };
    const handleDragOver = (event) => {
      event.preventDefault();
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
          const fileNames = imageState.images.map((img) => img.file.name).join(", ");

          onSendMessage(messageText, null, {
            isImageAnalysis: true,
            imageBase64: base64,
            imageFileName: fileNames,
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
        onDrop={handleImageDrop}
        onDragOver={handleDragOver}
        sx={{
          p: 2,
          borderTop: 1,
          borderColor: "divider",
          display: "flex",
          flexDirection: "column",
          gap: 1,
        }}
      >
        {/* File upload error */}
        {fileUploadState.error && (
          <CollapsibleAlert
            severity="error"
            sx={{ mb: 1 }}
            onClose={() =>
              setFileUploadState((prev) => ({ ...prev, error: null }))
            }
          >
            Upload failed: {fileUploadState.error}
          </CollapsibleAlert>
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
              {imageState.images.length < MAX_IMAGES && (
                <Typography variant="caption" color="text.secondary">
                  add up to {MAX_IMAGES - imageState.images.length} more
                </Typography>
              )}
            </Box>
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
          <CollapsibleAlert
            severity="error"
            sx={{ mb: 1 }}
            onClose={() => setImageState((prev) => ({ ...prev, error: null }))}
          >
            {imageState.error}
          </CollapsibleAlert>
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
            accept=".pdf,.txt,.csv,.docx,.md,.json,.py,.js,.jsx,.ts,.tsx,.html,.css,.xml,.yaml,.yml,image/*"
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