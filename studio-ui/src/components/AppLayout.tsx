import React, { useState } from 'react';
import { useNavigate, useLocation, Outlet } from 'react-router-dom';
import {
  Brand,
  Masthead,
  MastheadBrand,
  MastheadContent,
  MastheadMain,
  MastheadToggle,
  Nav,
  NavItem,
  NavList,
  Page,
  PageSidebar,
  PageSidebarBody,
  PageToggleButton,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
  SearchInput,
  NotificationBadge,
  PageSection,
  Flex,
  FlexItem,
  Avatar,
  Divider,
} from '@patternfly/react-core';
import {
  BarsIcon,
  BellIcon,
} from '@patternfly/react-icons';

const navItems = [
  { path: '/dashboard', label: 'Dashboard' },
  { path: '/agents', label: 'AI Crew' },
  { path: '/tasks', label: 'Tasks' },
  { path: '/files', label: 'Files' },
  { path: '/settings', label: 'Settings' },
];

const AppLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [searchValue, setSearchValue] = useState('');

  const onNavSelect = (
    _event: React.FormEvent<HTMLInputElement>,
    result: { itemId: number | string }
  ) => {
    navigate(result.itemId as string);
  };

  const headerToolbar = (
    <Toolbar id="toolbar" isFullHeight isStatic>
      <ToolbarContent>
        <ToolbarItem>
          <span style={{ color: 'white', fontSize: '0.875rem', opacity: 0.8 }}>
            Project /{' '}
            <strong style={{ opacity: 1 }}>opl-ai-software-team</strong>
          </span>
        </ToolbarItem>
        <ToolbarItem variant="separator" />
        <ToolbarItem>
          <SearchInput
            placeholder="Search tasks..."
            value={searchValue}
            onChange={(_event, value) => setSearchValue(value)}
            onClear={() => setSearchValue('')}
          />
        </ToolbarItem>
        <ToolbarItem>
          <NotificationBadge
            variant="attention"
            onClick={() => {}}
            aria-label="Notifications"
          >
            <BellIcon />
          </NotificationBadge>
        </ToolbarItem>
      </ToolbarContent>
    </Toolbar>
  );

  const masthead = (
    <Masthead style={{
      '--pf-v5-c-masthead--BackgroundColor': '#EE0000',
      '--pf-v5-c-masthead__main--before--BorderBottomColor': 'transparent',
      '--pf-v5-c-masthead--item-border-color--base': 'rgba(255,255,255,0.3)',
    } as React.CSSProperties}>
      <MastheadToggle>
        <PageToggleButton
          variant="plain"
          aria-label="Global navigation"
          isSidebarOpen={isSidebarOpen}
          onSidebarToggle={() => setIsSidebarOpen(!isSidebarOpen)}
          style={{ color: 'white' }}
        >
          <BarsIcon />
        </PageToggleButton>
      </MastheadToggle>
      <MastheadMain>
        <MastheadBrand>
          <Flex
            alignItems={{ default: 'alignItemsCenter' }}
            gap={{ default: 'gapSm' }}
          >
            <FlexItem>
              <Brand
                src="/redhat-logo.svg"
                alt="Red Hat"
                heights={{ default: '36px' }}
                style={{ filter: 'brightness(0) invert(1)' }}
              />
            </FlexItem>
            <FlexItem>
              <span
                style={{
                  color: 'white',
                  fontFamily: '"Red Hat Display", sans-serif',
                  fontWeight: 700,
                  fontSize: '1.25rem',
                  letterSpacing: '-0.01em',
                }}
              >
                AI Crew
              </span>
            </FlexItem>
          </Flex>
        </MastheadBrand>
      </MastheadMain>
      <MastheadContent>{headerToolbar}</MastheadContent>
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
                isActive={location.pathname === item.path}
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
            <Avatar src="" alt="Admin User" size="md" />
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
