import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Select,
  SelectOption,
  SelectList,
  MenuToggle,
  TextInputGroup,
  TextInputGroupMain,
  TextInputGroupUtilities,
  Button,
  Label,
} from '@patternfly/react-core';
import { TimesIcon } from '@patternfly/react-icons';
import { getJobs } from '../api/client';
import type { JobSummary } from '../types';

const statusColor = (s: string): 'green' | 'red' | 'blue' | 'orange' | 'grey' => {
  switch (s) {
    case 'running': return 'blue';
    case 'completed': return 'green';
    case 'failed':
    case 'quota_exhausted': return 'red';
    case 'cancelled': return 'orange';
    default: return 'grey';
  }
};

interface JobSearchSelectProps {
  selectedJobId: string | null;
  onSelect: (jobId: string) => void;
  style?: React.CSSProperties;
  'data-testid'?: string;
}

const JobSearchSelect: React.FC<JobSearchSelectProps> = ({
  selectedJobId,
  onSelect,
  style,
  ...rest
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [searchValue, setSearchValue] = useState('');
  const [options, setOptions] = useState<JobSummary[]>([]);
  const [selectedLabel, setSelectedLabel] = useState('');
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();
  const inputRef = useRef<HTMLInputElement>(null);

  const fetchJobs = useCallback(async (query: string) => {
    try {
      const res = await getJobs(1, 30, query || undefined);
      setOptions(res.jobs);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    fetchJobs('');
  }, [fetchJobs]);

  useEffect(() => {
    if (selectedJobId && options.length > 0) {
      const match = options.find((j) => j.id === selectedJobId);
      if (match) {
        const label = match.vision.substring(0, 35) + (match.vision.length > 35 ? '...' : '');
        setSelectedLabel(label);
      }
    } else if (!selectedJobId) {
      setSelectedLabel('');
    }
  }, [selectedJobId, options]);

  const handleSearchChange = (_event: React.FormEvent, value: string) => {
    setSearchValue(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => fetchJobs(value), 250);
  };

  const handleSelect = (_event: React.MouseEvent | undefined, value: string | number | undefined) => {
    const jobId = value as string;
    onSelect(jobId);
    setSearchValue('');
    setIsOpen(false);
    const match = options.find((j) => j.id === jobId);
    if (match) {
      setSelectedLabel(match.vision.substring(0, 35) + (match.vision.length > 35 ? '...' : ''));
    }
  };

  const handleClear = () => {
    setSearchValue('');
    fetchJobs('');
    inputRef.current?.focus();
  };

  const handleToggle = () => {
    if (!isOpen) {
      fetchJobs(searchValue);
    }
    setIsOpen(!isOpen);
  };

  const toggle = (toggleRef: React.Ref<HTMLButtonElement>) => (
    <MenuToggle
      ref={toggleRef}
      variant="typeahead"
      onClick={handleToggle}
      isExpanded={isOpen}
      isFullWidth
      style={{ minWidth: 240, ...style }}
      data-testid={rest['data-testid']}
    >
      <TextInputGroup isPlain>
        <TextInputGroupMain
          innerRef={inputRef}
          value={isOpen ? searchValue : (selectedLabel || '')}
          onChange={handleSearchChange}
          onFocus={() => {
            if (!isOpen) {
              setIsOpen(true);
              fetchJobs(searchValue);
            }
          }}
          autoComplete="off"
          placeholder="Search jobs..."
          aria-label="Search jobs"
        />
        {(searchValue || selectedLabel) && (
          <TextInputGroupUtilities>
            <Button
              variant="plain"
              onClick={(e) => {
                e.stopPropagation();
                handleClear();
              }}
              aria-label="Clear search"
              style={{ padding: '0 0.25rem' }}
            >
              <TimesIcon />
            </Button>
          </TextInputGroupUtilities>
        )}
      </TextInputGroup>
    </MenuToggle>
  );

  return (
    <Select
      isOpen={isOpen}
      selected={selectedJobId || undefined}
      onSelect={handleSelect}
      onOpenChange={setIsOpen}
      toggle={toggle}
    >
      <SelectList>
        {options.length === 0 ? (
          <SelectOption isDisabled value="__none__">
            No jobs found
          </SelectOption>
        ) : (
          options.map((job) => (
            <SelectOption key={job.id} value={job.id}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                <span style={{ fontSize: '0.85rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {job.vision.substring(0, 45)}{job.vision.length > 45 ? '...' : ''}
                </span>
                <Label isCompact color={statusColor(job.status)}>
                  {job.status === 'quota_exhausted' ? 'quota' : job.status}
                </Label>
              </div>
            </SelectOption>
          ))
        )}
      </SelectList>
    </Select>
  );
};

export default JobSearchSelect;
