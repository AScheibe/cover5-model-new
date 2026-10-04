import { Component, type ReactNode } from "react";

interface State {
  error: Error | null;
}

/** Keeps a failing panel (e.g. a chart) from blanking the whole page. */
export class ErrorBoundary extends Component<{ children: ReactNode; label: string; resetKey?: unknown }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(prev: { resetKey?: unknown }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (this.state.error) {
      return (
        <section className="panel" aria-label={this.props.label}>
          <h2>{this.props.label}</h2>
          <p className="field-error">This panel failed to render: {this.state.error.message}</p>
          <button type="button" className="btn" onClick={() => this.setState({ error: null })}>
            Try again
          </button>
        </section>
      );
    }
    return this.props.children;
  }
}
