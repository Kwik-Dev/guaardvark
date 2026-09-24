// frontend/src/pages/MCPServersPage.jsx
// MCP Servers page: the local MCP servers the agent can use, reached from
// Settings → Agents.

import React from "react";
import PageLayout from "../components/layout/PageLayout";
import MCPServersSection from "../components/settings/MCPServersSection";

const MCPServersPage = () => (
  <PageLayout title="MCP Servers" variant="standard">
    <MCPServersSection />
  </PageLayout>
);

export default MCPServersPage;
