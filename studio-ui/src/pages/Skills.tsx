import React, { useState, useEffect, useCallback } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  CardBody,
  CardTitle,
  EmptyState,
  EmptyStateBody,
  EmptyStateHeader,
  EmptyStateIcon,
  ExpandableSection,
  InputGroup,
  InputGroupItem,
  Label,
  LabelGroup,
  SearchInput,
  Spinner,
  Split,
  SplitItem,
  TextContent,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from '@patternfly/react-core';
import {
  BookOpenIcon,
  SearchIcon,
  SyncAltIcon,
  CubesIcon,
} from '@patternfly/react-icons';
import { getSkills, querySkills, reloadSkills } from '../api/client';
import type { SkillInfo, SkillSearchResult } from '../types';

const tagColor = (tag: string): 'blue' | 'green' | 'orange' | 'purple' | 'cyan' | 'grey' => {
  const map: Record<string, 'blue' | 'green' | 'orange' | 'purple' | 'cyan'> = {
    python: 'blue',
    frappe: 'green',
    devops: 'orange',
    docker: 'purple',
    scaffold: 'cyan',
    architecture: 'blue',
    compose: 'purple',
  };
  return map[tag.toLowerCase()] || 'grey';
};

const Skills: React.FC = () => {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [available, setAvailable] = useState(true);
  const [loading, setLoading] = useState(true);
  const [reloading, setReloading] = useState(false);

  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SkillSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [expandedResults, setExpandedResults] = useState<Record<number, boolean>>({});

  const loadSkills = useCallback(async () => {
    try {
      const data = await getSkills();
      setSkills(data.skills);
      setAvailable(data.available);
    } catch {
      setAvailable(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSkills();
  }, [loadSkills]);

  const handleSearch = async () => {
    if (!searchQuery.trim()) {
      setSearchResults(null);
      return;
    }
    setSearching(true);
    setExpandedResults({});
    try {
      const data = await querySkills(searchQuery.trim());
      setSearchResults(data.results);
    } catch {
      setSearchResults([]);
    } finally {
      setSearching(false);
    }
  };

  const handleReload = async () => {
    setReloading(true);
    try {
      await reloadSkills();
      await new Promise((r) => setTimeout(r, 2000));
      await loadSkills();
    } catch {
      // service down
    } finally {
      setReloading(false);
    }
  };

  const handleSearchKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSearch();
  };

  const allTags = Array.from(new Set(skills.flatMap((s) => s.tags))).sort();

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: '4rem' }}>
        <Spinner aria-label="Loading skills" />
      </div>
    );
  }

  return (
    <>
      {/* Header */}
      <div style={{ marginBottom: '1.5rem' }}>
        <Split hasGutter>
          <SplitItem isFilled>
            <Title headingLevel="h1" size="2xl" style={{ fontFamily: '"Red Hat Display", sans-serif' }}>
              Skills Library
            </Title>
            <p style={{ color: '#6A6E73', marginTop: '0.25rem' }}>
              Skills available to AI agents for framework-specific guidance.
              {skills.length > 0 && ` ${skills.length} skills loaded.`}
            </p>
          </SplitItem>
          <SplitItem>
            <Button
              variant="secondary"
              icon={<SyncAltIcon />}
              onClick={handleReload}
              isLoading={reloading}
              isDisabled={reloading || !available}
            >
              Reload Index
            </Button>
          </SplitItem>
        </Split>
      </div>

      {!available && (
        <Alert
          variant="warning"
          isInline
          title="Skills service unavailable"
          style={{ marginBottom: '1rem' }}
        >
          The skills service is not running. Agents will still work but without
          framework-specific guidance. Configure <code>SKILLS_SERVICE_URL</code> and
          start the skills-service container.
        </Alert>
      )}

      {/* Search bar */}
      <Toolbar style={{ marginBottom: '1rem', padding: 0 }}>
        <ToolbarContent style={{ padding: 0 }}>
          <ToolbarItem style={{ flex: 1 }}>
            <InputGroup>
              <InputGroupItem isFill>
                <SearchInput
                  placeholder="Semantic search — e.g. &quot;how to containerize a Frappe app&quot;"
                  value={searchQuery}
                  onChange={(_e, val) => setSearchQuery(val)}
                  onKeyDown={handleSearchKeyDown}
                  onClear={() => {
                    setSearchQuery('');
                    setSearchResults(null);
                  }}
                  isDisabled={!available}
                />
              </InputGroupItem>
              <InputGroupItem>
                <Button
                  variant="control"
                  onClick={handleSearch}
                  isDisabled={!available || !searchQuery.trim()}
                  isLoading={searching}
                  icon={<SearchIcon />}
                >
                  Search
                </Button>
              </InputGroupItem>
            </InputGroup>
          </ToolbarItem>
        </ToolbarContent>
      </Toolbar>

      {/* Tag summary */}
      {allTags.length > 0 && !searchResults && (
        <div style={{ marginBottom: '1rem' }}>
          <LabelGroup categoryName="Tags">
            {allTags.map((tag) => (
              <Label key={tag} color={tagColor(tag)} isCompact>
                {tag}
              </Label>
            ))}
          </LabelGroup>
        </div>
      )}

      {/* Search results */}
      {searchResults !== null && (
        <div style={{ marginBottom: '1.5rem' }}>
          <Title headingLevel="h3" size="lg" style={{ marginBottom: '0.75rem' }}>
            Search Results
            <Badge isRead style={{ marginLeft: '0.5rem' }}>{searchResults.length}</Badge>
          </Title>
          {searchResults.length === 0 ? (
            <EmptyState>
              <EmptyStateHeader
                titleText="No matching skills"
                icon={<EmptyStateIcon icon={SearchIcon} />}
                headingLevel="h4"
              />
              <EmptyStateBody>
                Try a different search query or broader terms.
              </EmptyStateBody>
            </EmptyState>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {searchResults.map((result, idx) => (
                <Card key={idx} isFlat isCompact>
                  <CardBody>
                    <Split hasGutter>
                      <SplitItem isFilled>
                        <strong style={{ fontSize: '0.9rem' }}>{result.skill_name}</strong>
                        <LabelGroup style={{ marginTop: '0.25rem' }}>
                          {result.tags.map((tag) => (
                            <Label key={tag} color={tagColor(tag)} isCompact>
                              {tag}
                            </Label>
                          ))}
                        </LabelGroup>
                      </SplitItem>
                    </Split>
                    <ExpandableSection
                      toggleText={expandedResults[idx] ? 'Hide content' : 'Show matched content'}
                      isExpanded={!!expandedResults[idx]}
                      onToggle={(_e, expanded) =>
                        setExpandedResults((prev) => ({ ...prev, [idx]: expanded }))
                      }
                      style={{ marginTop: '0.5rem' }}
                    >
                      <pre
                        style={{
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                          fontSize: '0.8rem',
                          backgroundColor: '#F5F5F5',
                          padding: '0.75rem',
                          borderRadius: 6,
                          maxHeight: 300,
                          overflow: 'auto',
                          fontFamily: '"JetBrains Mono", "Red Hat Mono", monospace',
                        }}
                      >
                        {result.content}
                      </pre>
                    </ExpandableSection>
                  </CardBody>
                </Card>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Skills grid */}
      {!searchResults && (
        <>
          {skills.length === 0 && available ? (
            <EmptyState>
              <EmptyStateHeader
                titleText="No skills loaded"
                icon={<EmptyStateIcon icon={CubesIcon} />}
                headingLevel="h4"
              />
              <EmptyStateBody>
                Drop <code>SKILL.md</code> files into the skills directory and
                click <strong>Reload Index</strong>.
              </EmptyStateBody>
            </EmptyState>
          ) : (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
                gap: '1rem',
              }}
            >
              {skills.map((skill) => (
                <Card key={skill.name} isFlat isCompact>
                  <CardTitle>
                    <Split hasGutter>
                      <SplitItem>
                        <div
                          style={{
                            width: 36,
                            height: 36,
                            borderRadius: 8,
                            backgroundColor: '#0066CC20',
                            color: '#0066CC',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            fontSize: '1rem',
                          }}
                        >
                          <BookOpenIcon />
                        </div>
                      </SplitItem>
                      <SplitItem isFilled>
                        <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>{skill.name}</div>
                        <div
                          style={{
                            fontSize: '0.75rem',
                            color: '#6A6E73',
                            marginTop: 2,
                          }}
                        >
                          {skill.file_count} file{skill.file_count !== 1 ? 's' : ''}
                        </div>
                      </SplitItem>
                    </Split>
                  </CardTitle>
                  <CardBody>
                    <TextContent>
                      <p style={{ fontSize: '0.8125rem', color: '#333', margin: 0 }}>
                        {skill.description || 'No description available.'}
                      </p>
                    </TextContent>
                    {skill.tags.length > 0 && (
                      <LabelGroup style={{ marginTop: '0.5rem' }}>
                        {skill.tags.map((tag) => (
                          <Label key={tag} color={tagColor(tag)} isCompact>
                            {tag}
                          </Label>
                        ))}
                      </LabelGroup>
                    )}
                  </CardBody>
                </Card>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
};

export default Skills;
