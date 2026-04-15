import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  name?: string;
}
interface State {
  hasError: boolean;
  error?: Error;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[ErrorBoundary:${this.props.name ?? 'unnamed'}]`, error, info);
  }

  private handleReset = () => {
    this.setState({ hasError: false, error: undefined });
  };

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div
            className="p-4 text-red-400 bg-red-900/20 rounded-lg m-2"
            role="alert"
            data-testid="error-boundary-fallback"
          >
            <p className="font-semibold">
              Something went wrong in {this.props.name || 'this section'}
            </p>
            <p className="text-sm text-red-300 mt-1">
              {this.state.error?.message}
            </p>
            <button
              onClick={this.handleReset}
              className="mt-2 text-sm text-sky-400 hover:underline"
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
