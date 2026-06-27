import React, { useState, useEffect } from 'react';

function useTick(ms = 10000) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), ms);
    return () => clearInterval(id);
  }, [ms]);
}

function timeSince(iso) {
  if (!iso) return null;
  const secs = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (secs < 60)   return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

function CreditsWidget({ credits }) {
  if (!credits) return null;
  const { remaining, used, quota, last_cost, updated_at } = credits;
  const pctUsed = quota ? Math.min(100, Math.round((used / quota) * 100)) : null;
  const pctLeft = 100 - pctUsed;
  const barColor = pctUsed >= 90 ? 'var(--red)' : pctUsed >= 70 ? 'var(--yellow)' : 'var(--green)';

  const updatedAgo = updated_at ? (() => {
    const secs = Math.floor((Date.now() - new Date(updated_at)) / 1000);
    if (secs < 60) return `${secs}s ago`;
    if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
    return `${Math.floor(secs / 3600)}h ago`;
  })() : null;

  return (
    <div className="credits-widget" title={`Updated ${updatedAgo ?? '—'} · Last call: ${last_cost ?? '?'} credit${last_cost !== 1 ? 's' : ''}`}>
      <span className="credits-label">API</span>
      <div className="credits-bar-track">
        <div className="credits-bar-fill" style={{ width: `${pctLeft}%`, background: barColor }} />
      </div>
      <span className="credits-count">
        {remaining?.toLocaleString() ?? '?'}<span className="credits-denom">/{quota?.toLocaleString()}</span>
      </span>
    </div>
  );
}

export default function StatusBar({
  status, lastRefresh, onRefresh,
  pollerPaused, onTogglePoller, pollerToggling,
  wakeOverride, onWake, onSleep, wakeToggling,
  credits,
  onForceSync, forceSyncing,
}) {
  useTick(10000);
  const lastPoll      = status?.last_poll       ? new Date(status.last_poll)       : null;
  const propsLastPoll = status?.props_last_poll ? new Date(status.props_last_poll) : null;
  const minsAgo      = lastPoll ? Math.floor((Date.now() - lastPoll) / 60000) : null;
  const quietMode    = status?.quiet_mode ?? false;
  const isStale      = minsAgo === null || minsAgo > 35;
  const secsToNext   = (status?.next_interval != null && lastPoll)
    ? Math.max(0, status.next_interval - (Date.now() - lastPoll) / 1000)
    : null;
  const pollImminent = !pollerPaused && secsToNext !== null && secsToNext < 60;

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

      <div className={`status-dot ${pollImminent ? 'imminent' : isStale || pollerPaused ? 'stale' : quietMode && !wakeOverride ? 'quiet' : 'live'}`} />

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
        {secsToNext !== null && !pollerPaused && (() => {
          const minsRemaining = Math.round(secsToNext / 60);
          return (
            <> · next poll in{' '}
              <strong className={pollImminent ? 'poll-imminent-text' : ''}>
                {minsRemaining > 0 ? `${minsRemaining}m` : '<1m'}
              </strong>
            </>
          );
        })()}
        {propsLastPoll && !pollerPaused && (
          <> · props <strong>{timeSince(propsLastPoll)}</strong></>
        )}
      </span>

      {lastRefresh && (
        <span className="status-text muted">
          · UI refreshed {timeSince(lastRefresh.toISOString())}
        </span>
      )}

      <CreditsWidget credits={credits} />

      <button
        className={`force-sync-btn ${forceSyncing ? 'syncing' : ''}`}
        onClick={onForceSync}
        disabled={forceSyncing}
        title="Force a full poll now — costs API credits"
      >
        {forceSyncing ? '⟳ Syncing…' : '⚡ Force Sync'}
      </button>

      <button className="refresh-btn" onClick={onRefresh} title="Refresh now">⟳</button>
    </div>
  );
}
