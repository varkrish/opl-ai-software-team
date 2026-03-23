import React, { useState, useEffect } from 'react';
import {
  Alert,
  Badge,
  Label,
  Spinner,
  Title,
} from '@patternfly/react-core';
import {
  Table,
  Thead,
  Tr,
  Th,
  Tbody,
  Td,
} from '@patternfly/react-table';
import {
  CheckCircleIcon,
  ExclamationCircleIcon,
  ExclamationTriangleIcon,
  InProgressIcon,
  PendingIcon,
} from '@patternfly/react-icons';
import type { ValidationIssue, ValidationReport } from '../types';

interface Props {
  jobId: string;
}

const STATUS_LABELS: Record<string, { color: 'green' | 'red' | 'blue' | 'orange'; icon: React.ReactNode }> = {
  completed: { color: 'green', icon: <CheckCircleIcon /> },
  failed:    { color: 'red',   icon: <ExclamationCircleIcon /> },
  running:   { color: 'blue',  icon: <InProgressIcon /> },
  pending:   { color: 'orange', icon: <PendingIcon /> },
};

const SEVERITY_LABELS: Record<string, 'red' | 'orange'> = {
  error:   'red',
  warning: 'orange',
};

export const ValidationReportPanel: React.FC<Props> = ({ jobId }) => {
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const fetchReport = async () => {
      try {
        const resp = await fetch(`/api/jobs/${jobId}/validation`);
        if (!resp.ok) {
          if (resp.status === 404) {
            setReport(null);
            return;
          }
          throw new Error(`HTTP ${resp.status}`);
        }
        const data: ValidationReport = await resp.json();
        if (!cancelled) setReport(data);
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchReport();
    return () => { cancelled = true; };
  }, [jobId]);

  if (loading) return <Spinner size="lg" />;
  if (error) return <Alert variant="danger" title="Failed to load validation report">{error}</Alert>;
  if (!report || report.issues.length === 0) return null;

  const { summary, overall, issues } = report;

  return (
    <div style={{ marginTop: '1rem' }}>
      <Title headingLevel="h3" size="lg" style={{ marginBottom: '0.5rem' }}>
        Validation Report
        <Badge
          style={{ marginLeft: '0.5rem' }}
          screenReaderText={overall}
        >
          {overall === 'PASS' ? 'PASS' : `${summary.failed} failed`}
        </Badge>
      </Title>

      <div style={{ display: 'flex', gap: '1rem', marginBottom: '1rem' }}>
        <Label color="grey">Total: {summary.total}</Label>
        <Label color="green" icon={<CheckCircleIcon />}>Fixed: {summary.fixed}</Label>
        <Label color="red" icon={<ExclamationCircleIcon />}>Failed: {summary.failed}</Label>
        <Label color="orange" icon={<PendingIcon />}>Pending: {summary.pending}</Label>
      </div>

      <Table aria-label="Validation issues" variant="compact">
        <Thead>
          <Tr>
            <Th>Status</Th>
            <Th>Severity</Th>
            <Th>Check</Th>
            <Th>File</Th>
            <Th>Description</Th>
            <Th>Fix Strategy</Th>
          </Tr>
        </Thead>
        <Tbody>
          {issues.map((issue: ValidationIssue) => {
            const sl = STATUS_LABELS[issue.status] || STATUS_LABELS.pending;
            return (
              <Tr key={issue.id}>
                <Td>
                  <Label color={sl.color} icon={sl.icon}>{issue.status}</Label>
                </Td>
                <Td>
                  <Label color={SEVERITY_LABELS[issue.severity] || 'orange'}>
                    {issue.severity}
                  </Label>
                </Td>
                <Td>{issue.check_name}</Td>
                <Td>
                  {issue.file_path || '-'}
                  {issue.line_number ? `:${issue.line_number}` : ''}
                </Td>
                <Td>{issue.description}</Td>
                <Td>{issue.fix_strategy || '-'}</Td>
              </Tr>
            );
          })}
        </Tbody>
      </Table>
    </div>
  );
};
