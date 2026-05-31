import React, { useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Chip,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Grid,
  IconButton,
  Divider,
} from '@mui/material';
import { Code, Map, GitMerge, X as CloseIcon } from 'lucide-react';
import { useTheme } from '@mui/material/styles';

const CodeRepoDashboard = ({ folder }) => {
  const theme = useTheme();
  const [mapOpen, setMapOpen] = useState(false);
  const [graphOpen, setGraphOpen] = useState(false);

  if (!folder || !folder.is_repository) {
    return null;
  }

  let metadata = {};
  try {
    metadata = folder.repo_metadata && typeof folder.repo_metadata === 'string'
      ? JSON.parse(folder.repo_metadata)
      : (folder.repo_metadata || {});
  } catch (e) {
    console.warn('Failed to parse repo_metadata', e);
    metadata = {};
  }

  const {
    languages = {},
    frameworks = [],
    file_count = 0,
    repository_map = '',
    dependency_graph = {},
    analyzed_at,
  } = metadata;

  const languageEntries = Array.isArray(languages)
    ? languages.map((lang) => [lang, null])
    : Object.entries(languages || {});

  const topLanguages = languageEntries
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  const frameworkList = Array.isArray(frameworks) ? frameworks : [];
  const dependencyGraph = dependency_graph && typeof dependency_graph === 'object' && !Array.isArray(dependency_graph)
    ? dependency_graph
    : {};
  const dependencyCount = Object.keys(dependencyGraph).length;

  const formatAnalyzedAt = (dateString) => {
    if (!dateString) return 'Never';
    const date = new Date(dateString);
    return Number.isNaN(date.getTime()) ? 'Unknown' : date.toLocaleString();
  };

  return (
    <Box sx={{ mb: 3 }}>
      <Card variant="outlined" sx={{ borderLeft: `4px solid ${theme.palette.primary.main}` }}>
        <CardContent>
          <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', mb: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Code size={24} color={theme.palette.primary.main} />
              <Typography variant="h6">Code Repository Intelligence</Typography>
            </Box>
            <Typography variant="caption" color="text.secondary">
              Analyzed: {formatAnalyzedAt(analyzed_at)}
            </Typography>
          </Box>

          {folder.description && (
            <Typography variant="body2" sx={{ mb: 2, whiteSpace: 'pre-wrap', maxHeight: '100px', overflowY: 'auto' }}>
              {folder.description}
            </Typography>
          )}

          <Grid container spacing={2} sx={{ mb: 2 }}>
            <Grid item xs={12} sm={6}>
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Languages & Frameworks
              </Typography>
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                {frameworkList.map((fw) => (
                  <Chip key={fw} label={fw} size="small" color="primary" variant="outlined" />
                ))}
                {topLanguages.map(([lang, count]) => (
                  <Chip key={lang} label={count == null ? lang : `${lang} (${count})`} size="small" variant="outlined" />
                ))}
                {frameworkList.length === 0 && topLanguages.length === 0 && (
                  <Chip label="Analysis pending" size="small" variant="outlined" />
                )}
              </Box>
            </Grid>
            <Grid item xs={12} sm={6}>
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Statistics
              </Typography>
              <Box sx={{ display: 'flex', gap: 1 }}>
                <Chip label={`${file_count} files`} size="small" />
                {dependencyCount > 0 && (
                  <Chip label={`${dependencyCount} mapped files`} size="small" />
                )}
              </Box>
            </Grid>
          </Grid>

          <Box sx={{ display: 'flex', gap: 1, mt: 2 }}>
            <Button
              variant="outlined"
              startIcon={<Map size={16} />}
              onClick={() => setMapOpen(true)}
              disabled={!repository_map}
              size="small"
            >
              View Repository Map
            </Button>
            <Button
              variant="outlined"
              startIcon={<GitMerge size={16} />}
              onClick={() => setGraphOpen(true)}
              disabled={dependencyCount === 0}
              size="small"
            >
              View Dependencies
            </Button>
          </Box>
        </CardContent>
      </Card>

      {/* Repository Map Dialog */}
      <Dialog open={mapOpen} onClose={() => setMapOpen(false)} maxWidth="lg" fullWidth>
        <DialogTitle sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          Repository Map
          <IconButton onClick={() => setMapOpen(false)} size="small">
            <CloseIcon size={20} />
          </IconButton>
        </DialogTitle>
        <Divider />
        <DialogContent>
          <Box sx={{ bgcolor: 'background.default', p: 2, borderRadius: 1, overflowX: 'auto' }}>
            <Typography variant="body2" component="pre" sx={{ fontFamily: 'monospace' }}>
              {repository_map || 'No map available.'}
            </Typography>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setMapOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>

      {/* Dependency Graph Dialog */}
      <Dialog open={graphOpen} onClose={() => setGraphOpen(false)} maxWidth="lg" fullWidth>
        <DialogTitle sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          Dependency Graph
          <IconButton onClick={() => setGraphOpen(false)} size="small">
            <CloseIcon size={20} />
          </IconButton>
        </DialogTitle>
        <Divider />
        <DialogContent>
          <Box sx={{ bgcolor: 'background.default', p: 2, borderRadius: 1, overflowX: 'auto' }}>
            <Typography variant="body2" component="pre" sx={{ fontFamily: 'monospace' }}>
              {JSON.stringify(dependencyGraph, null, 2)}
            </Typography>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setGraphOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default CodeRepoDashboard;
