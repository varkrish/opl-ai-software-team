import React, { useState } from 'react';
import {
  Card,
  CardTitle,
  CardBody,
  CardHeader,
  Title,
  Form,
  FormGroup,
  TextInput,
  MenuToggle,
  Select,
  SelectList,
  SelectOption,
  Switch,
  Button,
  Divider,
  Split,
  SplitItem,
  Alert,
  Tabs,
  Tab,
  TabTitleText,
  Grid,
  GridItem,
  Flex,
  FlexItem,
} from '@patternfly/react-core';
import { CogIcon, UserIcon, KeyIcon, BellIcon } from '@patternfly/react-icons';

const Settings: React.FC = () => {
  const [activeTabKey, setActiveTabKey] = useState<string | number>(0);
  const [saved, setSaved] = useState(false);

  // General Settings
  const [workspacePath, setWorkspacePath] = useState('./workspace');
  const [autoSaveEnabled, setAutoSaveEnabled] = useState(true);
  const [darkModeEnabled, setDarkModeEnabled] = useState(false);

  // API Settings
  const [apiKey, setApiKey] = useState('');
  const [apiEndpoint, setApiEndpoint] = useState('http://localhost:8080');
  const [modelSelectOpen, setModelSelectOpen] = useState(false);
  const [selectedModel, setSelectedModel] = useState('qwen3-14b');

  // Notification Settings
  const [emailNotifications, setEmailNotifications] = useState(true);
  const [slackNotifications, setSlackNotifications] = useState(false);
  const [jobCompletionNotif, setJobCompletionNotif] = useState(true);
  const [errorNotif, setErrorNotif] = useState(true);

  const models = [
    'qwen3-14b',
    'deepseek-r1-distill-qwen-14b',
    'granite-3-2-8b-instruct',
    'llama-3-70b',
    'gpt-4-turbo',
  ];

  const handleSave = () => {
    // In a real app, this would save to backend
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  return (
    <>
      {/* Header */}
      <Split hasGutter style={{ marginBottom: '1.5rem' }}>
        <SplitItem isFilled>
          <Title headingLevel="h1" size="2xl" style={{ fontFamily: '"Red Hat Display", sans-serif' }}>
            Settings
          </Title>
          <p style={{ color: '#6A6E73', marginTop: '0.25rem' }}>
            Configure your AI development environment and preferences.
          </p>
        </SplitItem>
        <SplitItem>
          <Button variant="primary" onClick={handleSave}>
            Save Changes
          </Button>
        </SplitItem>
      </Split>

      {saved && (
        <Alert
          variant="success"
          title="Settings saved successfully"
          style={{ marginBottom: '1.5rem' }}
          isInline
        />
      )}

      <Card>
        <CardBody>
          <Tabs
            activeKey={activeTabKey}
            onSelect={(_event, tabIndex) => setActiveTabKey(tabIndex)}
            aria-label="Settings tabs"
          >
            {/* General Tab */}
            <Tab eventKey={0} title={<TabTitleText><CogIcon /> General</TabTitleText>}>
              <div style={{ padding: '1.5rem 0' }}>
                <Title headingLevel="h3" size="lg" style={{ marginBottom: '1rem' }}>
                  General Settings
                </Title>
                <Form>
                  <FormGroup label="Workspace Path" fieldId="workspace-path" isRequired>
                    <TextInput
                      id="workspace-path"
                      value={workspacePath}
                      onChange={(_event, value) => setWorkspacePath(value)}
                      placeholder="/path/to/workspace"
                    />
                    <p style={{ fontSize: '0.875rem', color: '#6A6E73', marginTop: '0.5rem' }}>
                      Directory where all generated projects will be stored
                    </p>
                  </FormGroup>

                  <FormGroup label="Auto-save projects" fieldId="auto-save">
                    <Switch
                      id="auto-save"
                      label="Enabled"
                      labelOff="Disabled"
                      isChecked={autoSaveEnabled}
                      onChange={(_event, checked) => setAutoSaveEnabled(checked)}
                    />
                    <p style={{ fontSize: '0.875rem', color: '#6A6E73', marginTop: '0.5rem' }}>
                      Automatically save project state and progress
                    </p>
                  </FormGroup>

                  <FormGroup label="Dark Mode" fieldId="dark-mode">
                    <Switch
                      id="dark-mode"
                      label="Enabled"
                      labelOff="Disabled"
                      isChecked={darkModeEnabled}
                      onChange={(_event, checked) => setDarkModeEnabled(checked)}
                    />
                    <p style={{ fontSize: '0.875rem', color: '#6A6E73', marginTop: '0.5rem' }}>
                      Use dark theme for the interface (coming soon)
                    </p>
                  </FormGroup>
                </Form>
              </div>
            </Tab>

            {/* API Configuration Tab */}
            <Tab eventKey={1} title={<TabTitleText><KeyIcon /> API Configuration</TabTitleText>}>
              <div style={{ padding: '1.5rem 0' }}>
                <Title headingLevel="h3" size="lg" style={{ marginBottom: '1rem' }}>
                  API Configuration
                </Title>
                <Form>
                  <FormGroup label="API Endpoint" fieldId="api-endpoint" isRequired>
                    <TextInput
                      id="api-endpoint"
                      value={apiEndpoint}
                      onChange={(_event, value) => setApiEndpoint(value)}
                      placeholder="http://localhost:8080"
                    />
                    <p style={{ fontSize: '0.875rem', color: '#6A6E73', marginTop: '0.5rem' }}>
                      Backend API server URL
                    </p>
                  </FormGroup>

                  <FormGroup label="API Key" fieldId="api-key">
                    <TextInput
                      id="api-key"
                      type="password"
                      value={apiKey}
                      onChange={(_event, value) => setApiKey(value)}
                      placeholder="Enter your API key"
                    />
                    <p style={{ fontSize: '0.875rem', color: '#6A6E73', marginTop: '0.5rem' }}>
                      Optional: For secured API endpoints
                    </p>
                  </FormGroup>

                  <FormGroup label="Default LLM Model" fieldId="default-model">
                    <Select
                      toggle={(toggleRef) => (
                        <MenuToggle
                          ref={toggleRef}
                          onClick={() => setModelSelectOpen(!modelSelectOpen)}
                          isExpanded={modelSelectOpen}
                        >
                          {selectedModel}
                        </MenuToggle>
                      )}
                      onSelect={(_event, selection) => {
                        setSelectedModel(selection as string);
                        setModelSelectOpen(false);
                      }}
                      selected={selectedModel}
                      isOpen={modelSelectOpen}
                      onOpenChange={(isOpen) => setModelSelectOpen(isOpen)}
                      aria-label="Select default model"
                    >
                      <SelectList>
                        {models.map((model, index) => (
                          <SelectOption key={index} value={model}>
                            {model}
                          </SelectOption>
                        ))}
                      </SelectList>
                    </Select>
                    <p style={{ fontSize: '0.875rem', color: '#6A6E73', marginTop: '0.5rem' }}>
                      Language model used for code generation
                    </p>
                  </FormGroup>

                  <Divider style={{ margin: '1.5rem 0' }} />

                  <Alert
                    variant="info"
                    title="Model Configuration"
                    isInline
                    style={{ marginTop: '1rem' }}
                  >
                    Different agents use specialized models. Meta Agent uses{' '}
                    <strong>deepseek-r1-distill-qwen-14b</strong> for planning, while Dev Crew uses{' '}
                    <strong>qwen3-14b</strong> for implementation.
                  </Alert>
                </Form>
              </div>
            </Tab>

            {/* Notifications Tab */}
            <Tab eventKey={2} title={<TabTitleText><BellIcon /> Notifications</TabTitleText>}>
              <div style={{ padding: '1.5rem 0' }}>
                <Title headingLevel="h3" size="lg" style={{ marginBottom: '1rem' }}>
                  Notification Preferences
                </Title>
                <Form>
                  <Title headingLevel="h4" size="md" style={{ marginBottom: '0.75rem', marginTop: '1rem' }}>
                    Notification Channels
                  </Title>

                  <FormGroup label="Email Notifications" fieldId="email-notif">
                    <Switch
                      id="email-notif"
                      label="Enabled"
                      labelOff="Disabled"
                      isChecked={emailNotifications}
                      onChange={(_event, checked) => setEmailNotifications(checked)}
                    />
                  </FormGroup>

                  <FormGroup label="Slack Notifications" fieldId="slack-notif">
                    <Switch
                      id="slack-notif"
                      label="Enabled"
                      labelOff="Disabled"
                      isChecked={slackNotifications}
                      onChange={(_event, checked) => setSlackNotifications(checked)}
                    />
                    {slackNotifications && (
                      <TextInput
                        style={{ marginTop: '0.5rem' }}
                        placeholder="Slack webhook URL"
                        aria-label="Slack webhook URL"
                      />
                    )}
                  </FormGroup>

                  <Divider style={{ margin: '1.5rem 0' }} />

                  <Title headingLevel="h4" size="md" style={{ marginBottom: '0.75rem' }}>
                    Event Notifications
                  </Title>

                  <FormGroup label="Job Completion" fieldId="job-completion">
                    <Switch
                      id="job-completion"
                      label="Notify on completion"
                      labelOff="Don't notify"
                      isChecked={jobCompletionNotif}
                      onChange={(_event, checked) => setJobCompletionNotif(checked)}
                    />
                  </FormGroup>

                  <FormGroup label="Errors and Failures" fieldId="error-notif">
                    <Switch
                      id="error-notif"
                      label="Notify on errors"
                      labelOff="Don't notify"
                      isChecked={errorNotif}
                      onChange={(_event, checked) => setErrorNotif(checked)}
                    />
                  </FormGroup>
                </Form>
              </div>
            </Tab>

            {/* About Tab */}
            <Tab eventKey={3} title={<TabTitleText><UserIcon /> About</TabTitleText>}>
              <div style={{ padding: '1.5rem 0' }}>
                <Title headingLevel="h3" size="lg" style={{ marginBottom: '1rem' }}>
                  About AI Crew Studio
                </Title>

                <Grid hasGutter>
                  <GridItem span={6}>
                    <Card isFlat>
                      <CardHeader>
                        <CardTitle>Version Information</CardTitle>
                      </CardHeader>
                      <CardBody>
                        <Flex direction={{ default: 'column' }} gap={{ default: 'gapSm' }}>
                          <FlexItem>
                            <strong>Studio Version:</strong> 1.0.0
                          </FlexItem>
                          <FlexItem>
                            <strong>Backend API:</strong> v1.0.0
                          </FlexItem>
                          <FlexItem>
                            <strong>Database:</strong> SQLite (Persistent)
                          </FlexItem>
                          <FlexItem>
                            <strong>Build Date:</strong> 2026-02-06
                          </FlexItem>
                        </Flex>
                      </CardBody>
                    </Card>
                  </GridItem>

                  <GridItem span={6}>
                    <Card isFlat>
                      <CardHeader>
                        <CardTitle>System Resources</CardTitle>
                      </CardHeader>
                      <CardBody>
                        <Flex direction={{ default: 'column' }} gap={{ default: 'gapSm' }}>
                          <FlexItem>
                            <strong>Database Size:</strong> ~20KB
                          </FlexItem>
                          <FlexItem>
                            <strong>Active Agents:</strong> 6
                          </FlexItem>
                          <FlexItem>
                            <strong>Supported Models:</strong> 5+
                          </FlexItem>
                          <FlexItem>
                            <strong>Storage:</strong> File-based workspace
                          </FlexItem>
                        </Flex>
                      </CardBody>
                    </Card>
                  </GridItem>

                  <GridItem span={12}>
                    <Card isFlat style={{ marginTop: '1rem' }}>
                      <CardHeader>
                        <CardTitle>Documentation & Support</CardTitle>
                      </CardHeader>
                      <CardBody>
                        <Flex gap={{ default: 'gapMd' }}>
                          <FlexItem>
                            <Button variant="link" component="a" href="/README.md" target="_blank">
                              📚 Documentation
                            </Button>
                          </FlexItem>
                          <FlexItem>
                            <Button variant="link" component="a" href="/PERSISTENCE.md" target="_blank">
                              💾 Persistence Guide
                            </Button>
                          </FlexItem>
                          <FlexItem>
                            <Button variant="link" component="a" href="/TEST_REPORT.md" target="_blank">
                              ✅ Test Report
                            </Button>
                          </FlexItem>
                        </Flex>
                      </CardBody>
                    </Card>
                  </GridItem>
                </Grid>
              </div>
            </Tab>
          </Tabs>
        </CardBody>
      </Card>
    </>
  );
};

export default Settings;
