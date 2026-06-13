"use client";

import React from "react";

import { Button } from "@/components/ui/button";

interface Props {
  children: React.ReactNode;

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

  override componentDidUpdate(prevProps: Props) {
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false });
    }
  }

  override componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error("ErrorBoundary caught:", error, errorInfo);
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
              variant="link"
              size="sm"
              onClick={() => {
                this.setState({ hasError: false });
              }}
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
