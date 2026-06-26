import React from 'react';

function timeSince(iso) {
  if (!iso) return null;
  const secs = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (secs < 60)   return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

export default function StatusBar({
  status, lastRefresh, onRefresh,
  pollerPaused, onTogglePoller, pollerToggling,
  wakeOverride, onWake, onSleep, wakeToggling,
}) {
  const lastPoll  = status?.last_poll ? new Date(status.last_poll) : null;
  const minsAgo   = lastPoll ? Math.floor((Date.now() - lastPoll) / 60000) : null;
  const quietMode = status?.quiet_mode ?? false;
  const isStale   = minsAgo === null || minsAgo > 35;

  return (
    <div className="status-bar">

      {/* Pause / Resume */}
      <button
        className={`poller-toggle-btn ${pollerPaused ? 'paused' : 'live'}`}
        onClick={onTogglePoller}
        disabled={pollerToggling}
        title={pollerPaused ? 'Poller paused — click to resume' : 'Poller running — click to pause'}
      >
        {pollerToggling ? '…' : pollerPaused ? '▶ Resume' : '⏸ Pause'}
      </button>

      {/* Wake / Sleep — only shown during quiet hours */}
      {quietMode && !pollerPaused && (
        <button
          className={`poller-toggle-btn ${wakeOverride ? 'wake-active' : 'sleeping'}`}
          onClick={wakeOverride ? onSleep : onWake}
          disabled={wakeToggling}
          title={wakeOverride
            ? 'Override active — click to return to quiet mode'
            : 'Quiet hours active — click to wake and poll at normal speed'}
        >
          {wakeToggling ? '…' : wakeOverride ? '🌙 Sleep' : '☀ Wake'}
        </button>
      )}

      <div className={`status-dot ${isStale || pollerPaused ? 'stale' : quietMode && !wakeOverride ? 'quiet' : 'live'}`} />

      <span className="status-text">
        {pollerPaused ? (
          <span style={{ color: 'var(--yellow)' }}>Poller paused</span>
        ) : quietMode && !wakeOverride ? (
          <span style={{ color: 'var(--text-muted)' }}>
            Quiet hours ({status?.quiet_hours ?? ''})
            {lastPoll && <> · polled <strong>{timeSince(lastPoll)}</strong></>}
          </span>
        ) : lastPoll ? (
          <>polled <strong>{timeSince(lastPoll)}</strong></>
        ) : (
          'No poll yet'
        )}
        {status?.events_processed != null && !pollerPaused && (
          <> · <strong>{status.events_processed}</strong> events</>
        )}
        {status?.est_credits_cycle != null && !pollerPaused && (
          <> · ~<strong>{status.est_credits_cycle}</strong> credits/cycle</>
        )}
      </span>

      {lastRefresh && (
        <span className="status-text muted">
          · UI refreshed {timeSince(lastRefresh.toISOString())}
        </span>
      )}

      <button className="refresh-btn" onClick={onRefresh} title="Refresh now">⟳</button>
    </div>
  );
}
