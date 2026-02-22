import React, { useState } from 'react';
import { useNavigate, useLocation, Outlet } from 'react-router-dom';
import {
  Brand,
  Masthead,
  Nav,
  NavItem,
  NavList,
  Page,
  PageSidebar,
  PageSidebarBody,
  PageToggleButton,
  PageSection,
  Flex,
  FlexItem,
  Avatar,
  Divider,
} from '@patternfly/react-core';
import {
  BarsIcon,
} from '@patternfly/react-icons';

const navItems = [
  { path: '/dashboard', label: 'Dashboard' },
  { path: '/agents', label: 'AI Crew' },
  { path: '/tasks', label: 'Tasks' },
  { path: '/files', label: 'Files' },
  { path: '/migration', label: 'MTA Migration' },
  { path: '/refactor', label: 'Refactor' },
  { path: '/settings', label: 'Settings' },
];

const AppLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);

  const onNavSelect = (
    _event: React.FormEvent<HTMLInputElement>,
    result: { itemId: number | string }
  ) => {
    navigate(result.itemId as string);
  };

  const mastheadMinHeight = '4.375rem'; // PatternFly masthead default

  const masthead = (
    <Masthead style={{
      '--pf-v5-c-masthead--BackgroundColor': 'transparent',
      '--pf-v5-c-masthead__main--before--BorderBottomColor': 'transparent',
      '--pf-v5-c-masthead--item-border-color--base': 'transparent',
      padding: 0,
      display: 'flex',
      alignItems: 'stretch',
      minHeight: mastheadMinHeight,
    } as React.CSSProperties}>
      {/* ── Left section: white background with logo ── */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        minHeight: mastheadMinHeight,
        background: '#ffffff',
        padding: '0 1.25rem',
        gap: '0.75rem',
        borderBottom: '1px solid #e0e0e0',
        flexShrink: 0,
      }}>
        <PageToggleButton
          variant="plain"
          aria-label="Global navigation"
          isSidebarOpen={isSidebarOpen}
          onSidebarToggle={() => setIsSidebarOpen(!isSidebarOpen)}
          style={{ color: '#151515' }}
        >
          <BarsIcon />
        </PageToggleButton>
        <Brand
          src="/redhat-logo.svg"
          alt="Red Hat"
          heights={{ default: '36px' }}
        />
        <span
          style={{
            color: '#151515',
            fontFamily: '"Red Hat Display", sans-serif',
            fontWeight: 700,
            fontSize: '1.25rem',
            letterSpacing: '-0.01em',
            whiteSpace: 'nowrap',
          }}
        >
          AI Crew
        </span>
      </div>
      {/* ── Right section: red background ── */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'flex-end',
        minHeight: mastheadMinHeight,
        background: '#EE0000',
        flex: 1,
        padding: '0 1.5rem',
        borderBottom: '1px solid #cc0000',
      }}>
        <span style={{ color: 'rgba(255,255,255,0.7)', fontSize: '0.8125rem' }}>
          opl-ai-software-team
        </span>
      </div>
    </Masthead>
  );

  const sidebar = (
    <PageSidebar isSidebarOpen={isSidebarOpen}>
      <PageSidebarBody>
        <Nav onSelect={onNavSelect} aria-label="Main navigation">
          <NavList>
            {navItems.map((item) => (
              <NavItem
                key={item.path}
                itemId={item.path}
                isActive={
                  item.path === '/migration'
                    ? location.pathname.startsWith('/migration')
                    : item.path === '/refactor'
                      ? location.pathname.startsWith('/refactor')
                      : location.pathname === item.path
                }
              >
                {item.label}
              </NavItem>
            ))}
          </NavList>
        </Nav>
      </PageSidebarBody>
      <PageSidebarBody usePageInsets style={{ marginTop: 'auto' }}>
        <Divider style={{ marginBottom: '1rem' }} />
        <Flex
          alignItems={{ default: 'alignItemsCenter' }}
          gap={{ default: 'gapSm' }}
        >
          <FlexItem>
            <Avatar
              src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='48' height='48'%3E%3Ccircle cx='24' cy='24' r='24' fill='%23EE0000'/%3E%3Ctext x='24' y='24' dy='.35em' text-anchor='middle' fill='white' font-family='Red Hat Display,sans-serif' font-size='18' font-weight='700'%3EA%3C/text%3E%3C/svg%3E"
              alt="Admin User"
              size="md"
            />
          </FlexItem>
          <FlexItem>
            <div style={{ lineHeight: 1.3 }}>
              <div style={{ fontWeight: 600, fontSize: '0.875rem' }}>
                Admin User
              </div>
              <div style={{ fontSize: '0.75rem', opacity: 0.6 }}>
                admin@redhat.com
              </div>
            </div>
          </FlexItem>
        </Flex>
      </PageSidebarBody>
    </PageSidebar>
  );

  return (
    <Page header={masthead} sidebar={sidebar}>
      <PageSection isFilled>
        <Outlet />
      </PageSection>
    </Page>
  );
};

export default AppLayout;
