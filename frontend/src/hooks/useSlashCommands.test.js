import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import useSlashCommands from './useSlashCommands';
import { useAppStore } from '../stores/useAppStore';

function jsonResponse(data) {
  return {
    ok: true,
    json: async () => data,
  };
}

function mockFetch() {
  global.fetch.mockImplementation(async (url, options = {}) => {
    const path = String(url);

    if (path.startsWith('/api/rules')) {
      return jsonResponse({ data: { rules: [] } });
    }

    if (path.includes('/api/chat-sessions/') && path.endsWith('/mode')) {
      const body = options.body ? JSON.parse(options.body) : {};
      return jsonResponse({
        success: true,
        session_id: 'session_1',
        mode: body.mode || 'chat',
      });
    }

    if (path.includes('/api/chat/unified/') && path.endsWith('/abort')) {
      return jsonResponse({ success: true });
    }

    if (path === '/api/agent-control/kill') {
      return jsonResponse({ success: true });
    }

    return jsonResponse({ success: true });
  });
}

function renderSlashHook(overrides = {}) {
  const addMessage = vi.fn();
  const updateMessage = vi.fn();
  const onSendMessage = vi.fn();
  const setInputText = vi.fn();

  const hook = renderHook(() =>
    useSlashCommands({
      addMessage,
      updateMessage,
      onSendMessage,
      setInputText,
      chatState: {
        sessionId: 'session_1',
        projectId: null,
        clearMessages: vi.fn(),
        onPlanCreated: vi.fn(),
      },
      ...overrides,
    })
  );

  return { ...hook, addMessage, updateMessage, onSendMessage, setInputText };
}

describe('useSlashCommands mode switching', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch();
    useAppStore.setState({ sessionModes: {} });
  });

  it('executes exact optional /agent on Enter instead of inserting autocomplete text', async () => {
    const { result, addMessage, setInputText } = renderSlashHook();
    const event = {
      key: 'Enter',
      preventDefault: vi.fn(),
      stopPropagation: vi.fn(),
    };

    act(() => {
      result.current.handleInputChange('/agent');
    });

    await waitFor(() => {
      expect(result.current.popupVisible).toBe(true);
    });

    act(() => {
      result.current.handleKeyDown(event);
    });

    await waitFor(() => {
      expect(useAppStore.getState().getSessionMode('session_1')).toBe('agent');
    });

    expect(event.preventDefault).toHaveBeenCalled();
    expect(setInputText).toHaveBeenCalledWith('');
    expect(setInputText).not.toHaveBeenCalledWith('/agent ');
    expect(addMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        role: 'system',
        content: expect.stringContaining('agent mode'),
      })
    );
  });

  it('switches to agent mode and sends an immediate /agent task', async () => {
    const { result, onSendMessage } = renderSlashHook();

    await act(async () => {
      await result.current.executeCommand('/agent click the button');
    });

    expect(useAppStore.getState().getSessionMode('session_1')).toBe('agent');
    expect(onSendMessage).toHaveBeenCalledWith('click the button', null);
  });

  it('always PATCHes /chat even when local cache already says chat', async () => {
    useAppStore.getState().setSessionMode('session_1', 'chat');
    const { result, addMessage } = renderSlashHook();

    await act(async () => {
      await result.current.executeCommand('/chat');
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/chat-sessions/session_1/mode',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ mode: 'chat' }),
      })
    );
    expect(addMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        role: 'system',
        content: 'Already in chat mode.',
      })
    );
  });
});

describe('/websearch runs in the backend', () => {
  const SITEMAP = {
    success: true,
    url: 'https://site.example/sitemap.xml',
    final_url: 'https://site.example/sitemap.xml',
    type: 'urlset',
    total: 3,
    entries: [
      { loc: 'https://site.example/', priority: '1.0' },
      { loc: 'https://site.example/pricing', priority: '0.8', lastmod: '2026-09-01' },
    ],
    by_depth: { 0: 1, 1: 2 },
    landing_pages: [{ loc: 'https://site.example/pricing', priority: '0.8' }],
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch();
    useAppStore.setState({ sessionModes: {} });
  });

  function fetchedUrls() {
    return global.fetch.mock.calls.map(([url]) => String(url));
  }

  it('sends a query to the web_search tool', async () => {
    const { result, onSendMessage } = renderSlashHook();
    await act(async () => {
      await result.current.executeCommand('/websearch rust vs go');
    });
    expect(onSendMessage).toHaveBeenCalledWith('/websearch rust vs go', null, expect.objectContaining({
      direct_tool: 'web_search',
      direct_tool_params: { query: 'rust vs go' },
    }));
    expect(fetchedUrls().every((url) => url.startsWith('/api/'))).toBe(true);
  });

  it('audits one page with analyze_website for site:<address>', async () => {
    const { result, onSendMessage } = renderSlashHook();
    await act(async () => {
      await result.current.executeCommand('/websearch site:example.com');
    });
    expect(onSendMessage).toHaveBeenCalledWith('/websearch site:example.com', null, expect.objectContaining({
      direct_tool: 'analyze_website',
      direct_tool_params: { url: 'example.com' },
    }));
  });

  it('keeps site: with search words as a search', async () => {
    const { result, onSendMessage } = renderSlashHook();
    await act(async () => {
      await result.current.executeCommand('/websearch site:example.com pricing plans');
    });
    expect(onSendMessage).toHaveBeenCalledWith(expect.any(String), null, expect.objectContaining({
      direct_tool: 'web_search',
      direct_tool_params: { query: 'site:example.com pricing plans' },
    }));
  });

  it('asks the backend for a sitemap and reports it', async () => {
    global.fetch.mockImplementation(async (url) => {
      if (String(url) === '/api/web-search/sitemap') {
        return { ok: true, status: 200, json: async () => ({ success: true, data: SITEMAP }) };
      }
      return jsonResponse({ data: { rules: [] } });
    });
    const { result, addMessage, onSendMessage } = renderSlashHook();
    await act(async () => {
      await result.current.executeCommand('/websearch sitemap:https://site.example/sitemap.xml');
    });
    expect(global.fetch).toHaveBeenCalledWith('/api/web-search/sitemap', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ url: 'https://site.example/sitemap.xml' }),
    }));
    expect(fetchedUrls().some((url) => url.includes('site.example'))).toBe(false);
    expect(onSendMessage).not.toHaveBeenCalled();
    const report = addMessage.mock.calls.map(([m]) => m.content).join('\n');
    expect(report).toContain('3 page URLs.');
    expect(report).toContain('depth 1: 2');
    expect(report).toContain('https://site.example/pricing (priority 0.8)');
  });

  it('says why a sitemap was not read', async () => {
    global.fetch.mockImplementation(async (url) => {
      if (String(url) === '/api/web-search/sitemap') {
        return {
          ok: false,
          status: 403,
          json: async () => ({ success: false, message: 'Web access is disabled in system settings' }),
        };
      }
      return jsonResponse({ data: { rules: [] } });
    });
    const { result, addMessage } = renderSlashHook();
    await act(async () => {
      await result.current.executeCommand('/websearch sitemap:site.example/sitemap.xml');
    });
    expect(addMessage).toHaveBeenCalledWith(expect.objectContaining({
      role: 'system',
      content: 'Sitemap not read: Web access is disabled in system settings',
    }));
  });
});
