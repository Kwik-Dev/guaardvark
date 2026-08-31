import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import BreadcrumbNav from '../BreadcrumbNav';

describe('BreadcrumbNav', () => {
  it('scopes Home to the window root instead of filesystem root', () => {
    const onNavigate = vi.fn();
    render(
      <BreadcrumbNav
        currentPath="/Projects/Foo/shots"
        rootPath="/Projects/Foo"
        onNavigate={onNavigate}
      />,
    );
    expect(screen.getByText('shots')).toBeInTheDocument();
    expect(screen.queryByText('Projects')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Home'));
    expect(onNavigate).toHaveBeenCalledWith('/Projects/Foo');
  });

  it('still navigates to / when no rootPath is provided', () => {
    const onNavigate = vi.fn();
    render(
      <BreadcrumbNav
        currentPath="/Images/batch"
        onNavigate={onNavigate}
      />,
    );
    fireEvent.click(screen.getByText('Home'));
    expect(onNavigate).toHaveBeenCalledWith('/');
  });
});
