import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { ActionButton } from "../settings/ui";
import {
  ENABLE_HINT,
  ENABLE_LABEL,
  SEARCH_LABEL,
  SEARCH_SENT_TOOLTIP,
  SEARCH_TOOLTIP,
  WEB_SEARCH_OFFER_SEARCH,
} from "./webSearchOffer";

/**
 * The offer under a reply. Taking it is an action, so it is the kit's neutral
 * ActionButton rather than a SettingChip (a pill reads as on/off state).
 *
 * "search": calls onSearch(query) once and then stays disabled; onSearch
 * returns false when the chat could not send, which leaves it available.
 * Without onSearch there is no way to send, so nothing is shown.
 * "enable_web_access": sends nothing; a click says where the setting is.
 */
const WebSearchOfferChip = ({ offer, onSearch }) => {
  const [sent, setSent] = useState(false);
  const [hintShown, setHintShown] = useState(false);

  if (offer.action === WEB_SEARCH_OFFER_SEARCH) {
    if (!onSearch) return null;
    return (
      <Box sx={{ mt: 0.75 }}>
        <ActionButton
          data-testid="web-search-offer"
          disabled={sent}
          tooltip={sent ? SEARCH_SENT_TOOLTIP : SEARCH_TOOLTIP}
          onClick={() => {
            if (sent) return;
            setSent(onSearch(offer.query) !== false);
          }}
        >
          {SEARCH_LABEL}
        </ActionButton>
      </Box>
    );
  }

  return (
    <Box sx={{ mt: 0.75 }}>
      <ActionButton data-testid="web-search-offer" onClick={() => setHintShown(true)}>
        {ENABLE_LABEL}
      </ActionButton>
      {hintShown && (
        <Typography
          variant="caption"
          color="text.secondary"
          data-testid="web-search-offer-hint"
          sx={{ display: "block", mt: 0.5, fontSize: "0.7rem" }}
        >
          {ENABLE_HINT}
        </Typography>
      )}
    </Box>
  );
};

WebSearchOfferChip.propTypes = {
  offer: PropTypes.shape({
    action: PropTypes.string.isRequired,
    query: PropTypes.string.isRequired,
  }).isRequired,
  onSearch: PropTypes.func,
};

export default WebSearchOfferChip;
