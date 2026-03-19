"use client";

import React from "react";

interface Props {
  children: React.ReactNode;
  /** When this key changes (e.g. pathname), the error state resets automatically. */
  resetKey?: string;
  fallback?: React.ReactNode;
}

interface State {
  hasError: boolean;
}

export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidUpdate(prevProps: Props) {
    // Reset error state when resetKey changes (e.g. route navigation)
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false });
    }
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error("ErrorBoundary caught:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="flex h-full min-h-[200px] flex-col items-center justify-center gap-3 p-8">
            <p className="text-sm text-muted-foreground">
              Something went wrong rendering this section.
            </p>
            <button
              type="button"
              onClick={() => this.setState({ hasError: false })}
              className="text-sm text-primary underline hover:no-underline"
            >
              Try again
            </button>
          </div>
        )
      );
    }

    return this.props.children;
  }
}
