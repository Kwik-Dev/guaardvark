import React from "react";
import { Box, Fab, Badge, Tooltip, Zoom } from "@mui/material";
import { GuaardvarkLogo } from "../branding";
import { useAppStore } from "../../stores/useAppStore";
import { useFloatingChatStore } from "../../stores/useFloatingChatStore";

const FloatingChatFAB = () => {
  const isOpen = useFloatingChatStore((s) => s.isOpen);
  const toggleOpen = useFloatingChatStore((s) => s.toggleOpen);
  const hasMessages = useFloatingChatStore((s) => s.messages.length > 0);
  const systemLogo = useAppStore((s) => s.systemLogo);

  return (
    <Zoom in={!isOpen} unmountOnExit>
      <Tooltip title="Open chat (Ctrl+Shift+C)" placement="left">
        <Fab
          color="primary"
          onClick={toggleOpen}
          size="medium"
          sx={{
            position: "fixed",
            bottom: 40,
            right: 24,
            zIndex: 1400,
            bgcolor: "#000",
            color: "#fff",
            border: "1px solid rgba(255, 255, 255, 0.24)",
            boxShadow: "0 10px 24px rgba(0, 0, 0, 0.35)",
            "&:hover": {
              bgcolor: "#111",
              borderColor: "rgba(255, 255, 255, 0.6)",
            },
            "&:focus-visible": {
              boxShadow: "0 0 0 3px rgba(255, 255, 255, 0.18)",
            },
          }}
        >
          <Badge variant="dot" color="error" invisible={!hasMessages}>
            <Box
              component="span"
              sx={{
                width: 32,
                height: 32,
                borderRadius: "50%",
                bgcolor: "#000",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                overflow: "hidden",
              }}
            >
              {systemLogo ? (
                <Box
                  component="img"
                  src={`/api/uploads/${systemLogo}`}
                  alt="Guaardvark"
                  sx={{
                    width: 24,
                    height: 24,
                    objectFit: "contain",
                  }}
                />
              ) : (
                <GuaardvarkLogo size={24} color="#fff" />
              )}
            </Box>
          </Badge>
        </Fab>
      </Tooltip>
    </Zoom>
  );
};

export default FloatingChatFAB;
