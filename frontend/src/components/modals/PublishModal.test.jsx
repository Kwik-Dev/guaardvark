import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
    label: 'Bluesky',
    capabilities: {
      max_text_chars: 300,
      supports_title: false,
      supports_tags: false,
      visibilities: ['public'],
      default_visibility: 'public',
    },
  },
  {
    provider: 'youtube',
    label: 'YouTube',
    capabilities: {
      max_text_chars: 5000,
      supports_title: true,
      supports_tags: true,
      visibilities: ['private', 'unlisted', 'public'],
      default_visibility: 'private',
      public_opt_in: {
        visibilities: ['unlisted', 'public'],
        label: 'Allow public uploads',
        refusal: 'Public and unlisted uploads are off for this YouTube account.',
      },
    },
  },
  {
    provider: 'mastodon',
    label: 'Mastodon',
    capabilities: {
      max_text_chars: 500,
      visibilities: ['public', 'unlisted', 'private', 'direct'],
      default_visibility: 'unlisted',
    },
  },
];

// Its platform's default visibility is not the first in its list.
const MASTODON_CONNECTION = {
  id: 5,
  provider: 'mastodon',
  display_name: 'Mastodon account',
  enabled: true,
  has_credentials: true,
};

const REDDIT_CONNECTION = {
  id: 4,
  provider: 'reddit',
  display_name: 'Reddit account',
  enabled: true,
  has_credentials: true,
};

const REDDIT_SPEC = {
  provider: 'reddit',
  label: 'Reddit',
  capabilities: {
    max_text_chars: 40000,
    supports_title: true,
    requires_title: true,
    max_title_chars: 300,
    visibilities: ['public'],
  },
  post_fields: [
    { name: 'subreddit', label: 'Subreddit', required: true, help: '', placeholder: 'r/test' },
  ],
};

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

  it('composes a text-only post with the fields its platform needs', async () => {
    connectionsService.fetchConnections.mockResolvedValue([...CONNECTIONS, REDDIT_CONNECTION]);
    connectionsService.fetchProviders.mockResolvedValue([...SPECS, REDDIT_SPEC]);
    open({ documents: [] });

    expect(await screen.findByText('New post')).toBeInTheDocument();
    expect(screen.queryByLabelText('Subreddit')).not.toBeInTheDocument();

    fireEvent.click(await screen.findByText('Reddit account'));
    fireEvent.change(await screen.findByLabelText('Subreddit'), { target: { value: 'r/test' } });
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Hello' } });
    fireEvent.change(screen.getByLabelText('Text'), { target: { value: 'Body' } });
    // The title and the subreddit both say which destination needs them.
    expect(screen.getAllByText(/Required for Reddit\./)).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: /^publish$/i }));

    await waitFor(() =>
      expect(connectionsService.queuePublish).toHaveBeenCalledWith(
        expect.objectContaining({
          connection_ids: [4],
          document_ids: [],
          title: 'Hello',
          body: 'Body',
          post_options: { subreddit: 'r/test' },
        }),
      ),
    );
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

  describe('visibility', () => {
    beforeEach(() => {
      connectionsService.fetchConnections.mockResolvedValue([...CONNECTIONS, MASTODON_CONNECTION]);
    });

    it("shows a destination's declared default and sends it", async () => {
      open();

      fireEvent.click(await screen.findByText('Mastodon account'));
      expect(await screen.findByRole('combobox', { name: /^Visibility/ })).toHaveTextContent('unlisted');
      await waitFor(() =>
        expect(connectionsService.preflightPublish).toHaveBeenLastCalledWith(
          expect.objectContaining({ connection_ids: [5], visibilities: { 5: 'unlisted' } }),
        ),
      );

      fireEvent.click(screen.getByRole('button', { name: /^publish$/i }));
      await waitFor(() =>
        expect(connectionsService.queuePublish).toHaveBeenCalledWith(
          expect.objectContaining({ connection_ids: [5], visibilities: { 5: 'unlisted' } }),
        ),
      );
      expect(connectionsService.queuePublish.mock.calls[0][0]).not.toHaveProperty('visibility');
    });

    it('keeps each destination on its own visibility', async () => {
      open();

      fireEvent.click(await screen.findByText('Mastodon account'));
      fireEvent.click(screen.getByText('YouTube'));
      fireEvent.click(screen.getByText('Bluesky'));

      expect(await screen.findByRole('combobox', { name: /Visibility on Mastodon account/ })).toHaveTextContent(
        'unlisted',
      );
      expect(screen.getByRole('combobox', { name: /Visibility on YouTube/ })).toHaveTextContent('private');
      // One visibility only: no select, a line saying so.
      expect(screen.queryByRole('combobox', { name: /Visibility on Bluesky/ })).not.toBeInTheDocument();
      expect(screen.getByText('Public on Bluesky: the only visibility it offers.')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: /^publish$/i }));
      await waitFor(() =>
        expect(connectionsService.queuePublish).toHaveBeenCalledWith(
          expect.objectContaining({
            connection_ids: [5, 2, 1],
            visibilities: { 5: 'unlisted', 2: 'private', 1: 'public' },
          }),
        ),
      );
    });

    it('offers public and unlisted on YouTube only once its switch is on, and says why', async () => {
      open();

      fireEvent.click(await screen.findByText('YouTube'));
      const select = await screen.findByRole('combobox', { name: /^Visibility/ });
      expect(select).toHaveTextContent('private');
      expect(screen.getByText('Public and unlisted uploads are off for this YouTube account.')).toBeInTheDocument();
      fireEvent.mouseDown(select);
      const listbox = within(await screen.findByRole('listbox'));
      expect(listbox.getByRole('option', { name: 'public' })).toHaveAttribute('aria-disabled', 'true');
      expect(listbox.getByRole('option', { name: 'unlisted' })).toHaveAttribute('aria-disabled', 'true');
      expect(listbox.getByRole('option', { name: 'private' })).not.toHaveAttribute('aria-disabled');
    });

    it('lets a YouTube account with the switch on pick public', async () => {
      connectionsService.fetchConnections.mockResolvedValue([
        { ...CONNECTIONS[1], public_opt_in: { on: true, source: 'connection' } },
      ]);
      open();

      fireEvent.click(await screen.findByText('YouTube'));
      fireEvent.mouseDown(await screen.findByRole('combobox', { name: /^Visibility/ }));
      fireEvent.click(within(await screen.findByRole('listbox')).getByRole('option', { name: 'public' }));
      expect(screen.queryByText(/uploads are off/)).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: /^publish$/i }));
      await waitFor(() =>
        expect(connectionsService.queuePublish).toHaveBeenCalledWith(
          expect.objectContaining({ connection_ids: [2], visibilities: { 2: 'public' } }),
        ),
      );
    });

    it('sends the visibility the person picks for that destination only', async () => {
      open();

      fireEvent.click(await screen.findByText('Mastodon account'));
      fireEvent.click(screen.getByText('YouTube'));
      fireEvent.mouseDown(await screen.findByRole('combobox', { name: /Visibility on Mastodon account/ }));
      fireEvent.click(within(await screen.findByRole('listbox')).getByRole('option', { name: 'public' }));

      await waitFor(() =>
        expect(screen.getByRole('combobox', { name: /Visibility on Mastodon account/ })).toHaveTextContent('public'),
      );
      fireEvent.click(screen.getByRole('button', { name: /^publish$/i }));
      await waitFor(() =>
        expect(connectionsService.queuePublish).toHaveBeenCalledWith(
          expect.objectContaining({ visibilities: { 5: 'public', 2: 'private' } }),
        ),
      );
    });
  });
});
