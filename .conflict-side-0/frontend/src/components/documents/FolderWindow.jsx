// Complete folder window component
// Combines FolderWindowWrapper (chrome) and FolderContents (file list)

import React, { useState, useEffect, useCallback } from 'react';
import PropTypes from 'prop-types';
import { ToggleButtonGroup, ToggleButton, Tooltip, Box, Accordion, AccordionSummary, AccordionDetails, Chip, Typography } from '@mui/material';
import { ViewList as ViewListIcon, ViewModule as ViewModuleIcon, ViewComfy as ViewComfyIcon, ExpandMore as ExpandMoreIcon } from '@mui/icons-material';
import FolderWindowWrapper from './FolderWindowWrapper';
import FolderContents from './FolderContents';
import BreadcrumbNav from '../filesystem/BreadcrumbNav';
import { persistListingView, readStoredListingView } from './folderViewPolicy';

const FolderWindow = React.forwardRef(({
  _id,
  folder,
  isMinimized,
  onToggleMinimize,
  onClose,
  selectedItems,
  onSelectionChange,
  onItemsMove,
  onContextMenu,
  onDragStart,
  onDrop,
  onFolderOpen,
  onFileOpen,
  onFocusContext,
  refreshKey = 0,
  folderColors = {},
  ...gridLayoutProps
}, ref) => {
  // Track current path within this folder window for subfolder navigation
  const [currentPath, setCurrentPath] = useState(folder.path);
  // Whether this folder's contents contain visual media (images/videos)
  const [hasMedia, setHasMedia] = useState(false);
  // Per-window view mode — seeded from the last listing preference. Gallery is
  // opt-in for this window only and is never written back as a default.
  const [viewMode, setViewMode] = useState(() => readStoredListingView());

  const fallBackFromGallery = useCallback(() => {
    setHasMedia(false);
    setViewMode((prev) => (prev === 'media' ? readStoredListingView() : prev));
  }, []);

  // Reset path and leave gallery when the opened folder identity changes
  useEffect(() => {
    setCurrentPath(folder.path);
    fallBackFromGallery();
  }, [folder.id, folder.path, fallBackFromGallery]);

  // Detection only drives the Media toggle. Content never hijacks the view.
  const handleMediaDetected = useCallback((detected) => {
    setHasMedia(detected);
    if (!detected) {
      setViewMode((prev) => (prev === 'media' ? readStoredListingView() : prev));
    }
  }, []);

  // User toggle — persist list/grid as the default for future windows.
  // Gallery stays local to this window.
  const handleViewModeToggle = (_event, newViewMode) => {
    if (newViewMode === null) return;
    if (newViewMode === 'media') {
      setViewMode('media');
      return;
    }
    setViewMode(persistListingView(newViewMode));
  };

  // Handle navigation within folder window (for subfolder double-clicks and breadcrumb clicks)
  const handleNavigateToPath = (newPath) => {
    if (newPath === currentPath) return;
    setCurrentPath(newPath);
    fallBackFromGallery();
  };

  const titleBarActions = (
    <ToggleButtonGroup
      value={viewMode}
      exclusive
      onChange={handleViewModeToggle}
      size="small"
      sx={{ mr: 0.5 }}
    >
      <ToggleButton value="list" size="small" sx={{ minWidth: 'auto', px: 0.5 }}>
        <Tooltip title="List View">
          <ViewListIcon sx={{ fontSize: '14px' }} />
        </Tooltip>
      </ToggleButton>
      <ToggleButton value="grid" size="small" sx={{ minWidth: 'auto', px: 0.5 }}>
        <Tooltip title="Grid View">
          <ViewModuleIcon sx={{ fontSize: '14px' }} />
        </Tooltip>
      </ToggleButton>
      {hasMedia && (
        <ToggleButton value="media" size="small" sx={{ minWidth: 'auto', px: 0.5 }}>
          <Tooltip title="Media View">
            <ViewComfyIcon sx={{ fontSize: '14px' }} />
          </Tooltip>
        </ToggleButton>
      )}
    </ToggleButtonGroup>
  );

  return (
    <FolderWindowWrapper
      ref={ref}
      title={currentPath.split('/').filter(Boolean).pop() || folder.name}
      isMinimized={isMinimized}
      onToggleMinimize={onToggleMinimize}
      onClose={onClose}
      onDrop={onDrop}
      onDragOver={(e) => e.preventDefault()}
      titleBarActions={titleBarActions}
      isRepository={folder.is_repository}
      {...gridLayoutProps}
    >
      {/* Breadcrumb navigation for folder hierarchy */}
      {!isMinimized && (
        <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
          <BreadcrumbNav
            currentPath={currentPath}
            rootPath={folder.path}
            onNavigate={handleNavigateToPath}
          />
        </Box>
      )}

      {!isMinimized && folder.is_repository && folder.repo_metadata && (
        <Box sx={{ px: 1, pb: 1 }}>
          <Accordion defaultExpanded={false} sx={{ boxShadow: 'none', '&:before': { display: 'none' } }}>
            <AccordionSummary expandIcon={<ExpandMoreIcon />} sx={{ minHeight: 32, '& .MuiAccordionSummary-content': { my: 0.5 } }}>
              <Typography variant="caption" sx={{ display: 'flex', alignItems: 'center', gap: 0.5, fontWeight: 'bold' }}>
                Repository Analysis
              </Typography>
            </AccordionSummary>
            <AccordionDetails sx={{ pt: 0 }}>
              {folder.repo_metadata.frameworks?.length > 0 && (
                <Box sx={{ mb: 1, display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                  {folder.repo_metadata.frameworks.map(fw => (
                    <Chip key={fw} label={fw} size="small" color="primary" variant="outlined" />
                  ))}
                </Box>
              )}
              {folder.repo_metadata.languages && (
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                  Languages: {Object.entries(folder.repo_metadata.languages)
                    .sort(([,a], [,b]) => b - a)
                    .slice(0, 5)
                    .map(([ext, count]) => `${ext} (${count})`)
                    .join(', ')}
                </Typography>
              )}
              {folder.description && (
                <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', maxHeight: 200, overflow: 'auto', fontSize: '0.75rem' }}>
                  {folder.description}
                </Typography>
              )}
            </AccordionDetails>
          </Accordion>
        </Box>
      )}

      <Box
        data-folder-path={currentPath}
        onMouseDown={() => onFocusContext && onFocusContext({ type: 'folder', path: currentPath, folderId: folder.id })}
        onDragOver={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        onDrop={onDrop}
        sx={{ height: '100%', width: '100%', overflow: 'auto' }}
      >
        <FolderContents
          folder={folder}
          currentPath={currentPath}
          onNavigateToPath={handleNavigateToPath}
          viewMode={viewMode}
          selectedItems={selectedItems}
          onSelectionChange={onSelectionChange}
          onItemsMove={onItemsMove}
          onContextMenu={onContextMenu}
          onDragStart={onDragStart}
          onDrop={onDrop}
          onFolderOpen={onFolderOpen}
          onFileOpen={onFileOpen}
          onFocusContext={onFocusContext}
          refreshKey={refreshKey}
          folderColors={folderColors}
          onMediaDetected={handleMediaDetected}
        />
      </Box>
    </FolderWindowWrapper>
  );
});

FolderWindow.displayName = 'FolderWindow';

FolderWindow.propTypes = {
  id: PropTypes.string.isRequired,
  folder: PropTypes.shape({
    name: PropTypes.string.isRequired,
    path: PropTypes.string.isRequired,
    is_repository: PropTypes.bool,
  }).isRequired,
  isMinimized: PropTypes.bool,
  onToggleMinimize: PropTypes.func,
  onClose: PropTypes.func.isRequired,
  selectedItems: PropTypes.instanceOf(Set),
  onSelectionChange: PropTypes.func,
  onItemsMove: PropTypes.func,
  onContextMenu: PropTypes.func,
  onDragStart: PropTypes.func,
  onDrop: PropTypes.func,
  onFolderOpen: PropTypes.func,
  onFocusContext: PropTypes.func,
  refreshKey: PropTypes.number,
};

export default FolderWindow;
