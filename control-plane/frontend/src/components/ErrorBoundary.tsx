import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  caught: Error | null;
}

/** F10: keeps an unexpected render exception from unmounting the whole app. */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { caught: null };

  static getDerivedStateFromError(caught: Error): ErrorBoundaryState {
    return { caught };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Control Panel render error", error, info.componentStack);
  }

  render(): ReactNode {
    const { caught } = this.state;
    if (caught === null) return this.props.children;
    return (
      <div className="state-card error-card" role="alert">
        <strong>Something went wrong</strong>
        <span>{caught.message || "An unexpected rendering error occurred."}</span>
        <button type="button" className="secondary-button" onClick={() => window.location.reload()}>
          Reload the Control Panel
        </button>
      </div>
    );
  }
}
