"""Tests for the ImageCog (/imagine and /enhance-prompt commands)."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from commands.image import ImageCog
from core.api_client import APIError


@pytest.fixture
def image_cog(mock_api_client, sample_config):
    bot = MagicMock()
    return ImageCog(bot=bot, api_client=mock_api_client, config=sample_config)


class TestImagineCommand:
    @pytest.mark.asyncio
    async def test_imagine_defers_and_starts(self, image_cog, mock_interaction, mock_api_client):
        """Imagine should defer, call generate_image, then poll and send result."""
        with patch("commands.image.asyncio.sleep", new_callable=AsyncMock):
            await image_cog._handle_imagine(mock_interaction, "a cute cat")

        mock_interaction.response.defer.assert_awaited_once()
        mock_api_client.generate_image.assert_awaited_once()
        mock_interaction.followup.send.assert_awaited()
        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("file") is not None

    @pytest.mark.asyncio
    async def test_imagine_polls_until_complete(self, image_cog, mock_interaction, mock_api_client):
        """Should poll get_batch_status until it returns completed."""
        # First call returns pending, second returns completed
        mock_api_client.get_batch_status.side_effect = [
            {"status": "processing", "results": []},
            {
                "status": "completed",
                "results": [{"success": True, "image_path": "/tmp/img.png"}],
            },
        ]

        with patch("commands.image.asyncio.sleep", new_callable=AsyncMock):
            await image_cog._handle_imagine(mock_interaction, "a landscape")

        assert mock_api_client.get_batch_status.await_count == 2
        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("file") is not None

    @pytest.mark.asyncio
    async def test_imagine_handles_generation_failure(self, image_cog, mock_interaction, mock_api_client):
        """If generation fails, report the error."""
        mock_api_client.get_batch_status.return_value = {
            "status": "failed",
            "error": "GPU out of memory",
        }

        with patch("commands.image.asyncio.sleep", new_callable=AsyncMock):
            await image_cog._handle_imagine(mock_interaction, "something big")

        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        assert "failed" in call_kwargs.get("content", "").lower()

    @pytest.mark.asyncio
    async def test_imagine_rejects_dms(self, image_cog, mock_interaction):
        """Image generation should not work in DMs."""
        mock_interaction.guild = None

        await image_cog._handle_imagine(mock_interaction, "a cat")

        mock_interaction.response.send_message.assert_awaited_once()
        call_args = mock_interaction.response.send_message.call_args
        content = call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        assert "not available in dms" in content.lower()

    @pytest.mark.asyncio
    async def test_imagine_decrements_active_jobs(self, image_cog, mock_interaction, mock_api_client):
        """Active jobs counter should return to 0 after completion."""
        with patch("commands.image.asyncio.sleep", new_callable=AsyncMock):
            await image_cog._handle_imagine(mock_interaction, "test")

        assert image_cog._active_jobs == 0

    @pytest.mark.asyncio
    async def test_imagine_passes_subject_ids_for_character(
        self, image_cog, mock_interaction, mock_api_client
    ):
        """Picking a Cast character must send subject_ids to the batch API."""
        with patch("commands.image.asyncio.sleep", new_callable=AsyncMock):
            await image_cog._handle_imagine(
                mock_interaction, "walking through Gotham rain", character="26"
            )

        mock_api_client.generate_image.assert_awaited_once()
        kwargs = mock_api_client.generate_image.await_args.kwargs
        assert kwargs.get("subject_ids") == [26]
        assert kwargs.get("width") == 1024
        assert kwargs.get("height") == 1024
        content = mock_interaction.followup.send.call_args.kwargs.get("content", "")
        assert "Batman 2" in content or "Cast" in content

    @pytest.mark.asyncio
    async def test_imagine_rejects_unknown_character(
        self, image_cog, mock_interaction, mock_api_client
    ):
        await image_cog._handle_imagine(
            mock_interaction, "a scene", character="no_such_hero"
        )
        mock_api_client.generate_image.assert_not_awaited()
        content = mock_interaction.followup.send.call_args.kwargs.get("content", "")
        assert "unknown character" in content.lower()

    @pytest.mark.asyncio
    async def test_imagine_uses_modern_defaults(
        self, image_cog, mock_interaction, mock_api_client
    ):
        with patch("commands.image.asyncio.sleep", new_callable=AsyncMock):
            await image_cog._handle_imagine(mock_interaction, "a cute cat")

        kwargs = mock_api_client.generate_image.await_args.kwargs
        assert kwargs.get("steps") == 9
        assert kwargs.get("width") == 1024
        assert kwargs.get("height") == 1024


class TestEnhancePromptCommand:
    @pytest.mark.asyncio
    async def test_enhance_returns_improved_prompt(self, image_cog, mock_interaction, mock_api_client):
        """Enhance prompt should return an embed with original, enhanced, and negative prompts."""
        await image_cog._handle_enhance(mock_interaction, "a landscape")

        mock_interaction.response.defer.assert_awaited_once()
        mock_interaction.followup.send.assert_awaited_once()
        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None
        field_names = [f.name for f in embed.fields]
        assert "Original" in field_names
        assert "Enhanced" in field_names
        assert "Negative Prompt" in field_names

    @pytest.mark.asyncio
    async def test_enhance_handles_api_error(self, image_cog, mock_interaction, mock_api_client):
        """API errors should be reported."""
        mock_api_client.enhance_prompt.side_effect = APIError("Service down", 500)

        await image_cog._handle_enhance(mock_interaction, "test prompt")

        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        assert "failed" in call_kwargs.get("content", "").lower()

    @pytest.mark.asyncio
    async def test_enhance_empty_prompt(self, image_cog, mock_interaction):
        """Empty prompt after sanitization should be rejected."""
        await image_cog._handle_enhance(mock_interaction, "<@!12345>")

        mock_interaction.response.send_message.assert_awaited_once()
        call_kwargs = mock_interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True
