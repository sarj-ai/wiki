"use client";

import React from "react";

import { Button } from "@/components/ui/button";

// eslint-disable-next-line @sarj/require-interface-for-exported-class -- React.Component already supplies the lifecycle contract; this boundary adds no consumer API.
export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  override componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error("ErrorBoundary caught:", error, errorInfo);
  }

  override componentDidUpdate(prevProps: Props) {
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false });
    }
  }

  override render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="flex h-full min-h-[200px] flex-col items-center justify-center gap-3 p-8">
            <p className="text-muted-foreground text-sm">
              Something went wrong rendering this section.
            </p>
            <Button
              onClick={() => {
                this.setState({ hasError: false });
              }}
              size="sm"
              variant="link"
            >
              Try again
            </Button>
          </div>
        )
      );
    }

    return this.props.children;
  }
}

interface Props {
  children: React.ReactNode;

  fallback?: React.ReactNode;
  resetKey?: string;
}

interface State {
  hasError: boolean;
}
