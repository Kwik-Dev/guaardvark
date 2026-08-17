import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import PublishModal from './PublishModal';
import * as connectionsService from '../../api/connectionsService';

vi.mock('../../api/connectionsService');

const CONNECTIONS = [
  {
    id: 1,
    provider: 'bluesky',
    display_name: 'Bluesky',
    enabled: true,
    has_credentials: true,
  },
  {
    id: 2,
    provider: 'youtube',
    display_name: 'YouTube',
    enabled: true,
    has_credentials: true,
  },
  {
    id: 3,
    provider: 'mastodon',
    display_name: 'No creds',
    enabled: true,
    has_credentials: false,
  },
];

const SPECS = [
  {
    provider: 'bluesky',
    capabilities: {
      max_text_chars: 300,
      supports_title: false,
      supports_tags: false,
      visibilities: ['public'],
    },
  },
  {
    provider: 'youtube',
    capabilities: {
      max_text_chars: 5000,
      supports_title: true,
      supports_tags: true,
      visibilities: ['private', 'unlisted', 'public'],
    },
  },
  {
    provider: 'mastodon',
    capabilities: { max_text_chars: 500, visibilities: ['public'] },
  },
];

const DOCUMENT = {
  id: 42,
  filename: 'render.png',
  metadata: { original_prompt: 'a neon city' },
  tags: ['neon', 'city'],
};

describe('PublishModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    connectionsService.fetchConnections.mockResolvedValue(CONNECTIONS);
    connectionsService.fetchProviders.mockResolvedValue(SPECS);
    connectionsService.preflightPublish.mockResolvedValue({
      ok: true,
      per_connection: {},
    });
    connectionsService.queuePublish.mockResolvedValue({
      count: 1,
      requires_approval: false,
    });
  });

  const open = (props = {}) =>
    render(
      <PublishModal open onClose={vi.fn()} documents={[DOCUMENT]} {...props} />,
    );

  it('prefills the caption from the asset prompt', async () => {
    open();
    expect(await screen.findByDisplayValue('a neon city')).toBeInTheDocument();
  });

  it('disables a connection that has no credentials', async () => {
    open();
    const chip = await screen.findByText('No creds');
    expect(chip.closest('.MuiChip-root')).toHaveClass('Mui-disabled');
  });

  it('counts characters against the strictest selected target', async () => {
    open();

    fireEvent.click(await screen.findByText('Bluesky'));
    expect(await screen.findByText(/\/ 300 characters/)).toBeInTheDocument();

    // Adding YouTube (5000) must not relax Bluesky's 300 limit.
    fireEvent.click(screen.getByText('YouTube'));
    expect(await screen.findByText(/\/ 300 characters/)).toBeInTheDocument();
  });

  it('blocks publishing when the caption exceeds the limit', async () => {
    open({ documents: [{ ...DOCUMENT, metadata: { original_prompt: 'x'.repeat(400) } }] });

    fireEvent.click(await screen.findByText('Bluesky'));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /publish/i })).toBeDisabled(),
    );
  });

  it('surfaces preflight violations from the backend', async () => {
    connectionsService.preflightPublish.mockResolvedValue({
      ok: false,
      per_connection: {
        1: { label: 'Bluesky', violations: ['This target does not accept video.'] },
      },
    });
    open();

    fireEvent.click(await screen.findByText('Bluesky'));
    expect(
      await screen.findByText(/Bluesky: This target does not accept video\./),
    ).toBeInTheDocument();
  });

  it('shows a title field only when a selected target supports one', async () => {
    open();

    fireEvent.click(await screen.findByText('Bluesky'));
    expect(screen.queryByLabelText('Title')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('YouTube'));
    expect(await screen.findByLabelText('Title')).toBeInTheDocument();
  });

  it('submits the selected targets and document', async () => {
    const onClose = vi.fn();
    open({ onClose });

    fireEvent.click(await screen.findByText('Bluesky'));
    fireEvent.click(screen.getByRole('button', { name: /^publish$/i }));

    await waitFor(() =>
      expect(connectionsService.queuePublish).toHaveBeenCalledWith(
        expect.objectContaining({
          connection_ids: [1],
          document_ids: [42],
          requested_by: 'ui',
        }),
      ),
    );
    expect(onClose).toHaveBeenCalled();
  });
});
