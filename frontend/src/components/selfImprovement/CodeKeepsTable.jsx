// Code keeps: swarm arms the autoresearch director staged as pending fixes,
// approved or rejected here. Shown on the Autoresearch and Self-improvement
// pages.
import React from "react";
import {
  Button,
  Chip,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";

/** A server time as a local date; a time without a zone is UTC. */
export const formatServerTime = (iso) => {
  if (!iso) return "—";
  try {
    const text = String(iso);
    return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(text) ? text : `${text}Z`).toLocaleString();
  } catch {
    return iso;
  }
};

const formatDate = formatServerTime;

export default function CodeKeepsTable({ keeps, onDecide }) {
  if (!keeps || keeps.length === 0) return null;
  return (
    <Paper
      elevation={0}
      sx={{ p: 2, mb: 3, border: 1, borderColor: "divider" }}
    >
      <Typography variant="h6" sx={{ mb: 1 }}>
        Code keeps
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
        Swarm arms that passed preserve-and-extend. Approve here, then apply
        from Settings — nothing auto-merges to main.
      </Typography>
      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Description</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Created</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {codeKeeps.map((fix) => (
              <TableRow key={fix.id}>
                <TableCell>{fix.fix_description}</TableCell>
                <TableCell>
                  <Chip label={fix.status} size="small" sx={{ height: 20, fontSize: "0.7rem" }} />
                </TableCell>
                <TableCell>{formatDate(fix.created_at)}</TableCell>
                <TableCell align="right">
                  {fix.status === "proposed" && (
                    <>
                      <Button size="small" onClick={() => onDecide(fix.id, "approve")}>
                        Approve
                      </Button>
                      <Button size="small" color="warning" onClick={() => onDecide(fix.id, "reject")}>
                        Reject
                      </Button>
                    </>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Paper>
  );
}
