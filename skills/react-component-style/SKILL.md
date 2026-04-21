---
name: react-component-style
description: >-
  React component patterns including hooks usage, memoization, and PatternFly
  component conventions. Use when building React frontends.
tags: [react, frontend, typescript]
---

# React Component Style Guide

## Hooks

Prefer `useCallback` for event handlers passed to child components:

```tsx
const handleClick = useCallback((id: string) => {
  setSelected(id);
}, []);
```

## Memoization

Use `React.memo` for components that receive stable props but re-render due to parent changes.

## PatternFly

Use PatternFly components for consistency:

```tsx
import { Button, Alert } from '@patternfly/react-core';
```
