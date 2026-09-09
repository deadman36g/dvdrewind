/**
 * DVDRewind Archive Control Center Client
 * Handles real-time monitoring, incremental sync, poster backfills, and database optimization.
 */

let archivePollTimer = null;
let isArchiveModalOpen = false;
let lastLogLinesLength = 0;

function openArchiveModal() {
  const modal = document.getElementById("archive-modal");
  if (!modal) return;
  modal.style.display = "flex";
  isArchiveModalOpen = true;
  pollArchiveStatus();
  // Accelerate polling while modal is open
  startArchivePolling(1000);
}

function closeArchiveModal() {
  const modal = document.getElementById("archive-modal");
  if (!modal) return;
  modal.style.display = "none";
  isArchiveModalOpen = false;
  // Slow polling down when modal is closed
  startArchivePolling(20000);
}

function startArchivePolling(ms) {
  if (archivePollTimer) clearInterval(archivePollTimer);
  archivePollTimer = setInterval(pollArchiveStatus, ms);
}

async function pollArchiveStatus() {
  try {
    const res = await fetch("/api/archive/status");
    if (!res.ok) return;
    const data = await res.json();
    renderArchiveStatus(data);
  } catch (err) {
    console.debug("Archive status poll error:", err);
  }
}

function formatRelativeTime(isoStr) {
  if (!isoStr) return "Never";
  try {
    const date = new Date(isoStr);
    const now = new Date();
    const diffSec = Math.floor((now - date) / 1000);
    if (diffSec < 60) return "Just now";
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    return date.toLocaleDateString();
  } catch (e) {
    return isoStr;
  }
}

function renderArchiveStatus(data) {
  const pulseDot = document.getElementById("archive-pulse-dot");
  const navLabel = document.getElementById("archive-status-label");
  const navBtn = document.getElementById("archive-status-btn");
  const consolePulse = document.getElementById("console-pulse-dot");

  // Header button status
  if (data.is_running) {
    if (pulseDot) pulseDot.className = "archive-pulse-dot pulsing";
    if (consolePulse) consolePulse.className = "console-dot amber pulse";
    if (navBtn) navBtn.classList.add("is-running");
    let label = "Working...";
    if (data.task_type === "sync") label = "Syncing...";
    else if (data.task_type === "posters") label = "Posters...";
    else if (data.task_type === "vacuum") label = "Optimizing...";
    if (navLabel) navLabel.textContent = label;
  } else {
    if (pulseDot) pulseDot.className = "archive-pulse-dot";
    if (consolePulse) consolePulse.className = "console-dot green";
    if (navBtn) navBtn.classList.remove("is-running");
    if (navLabel) navLabel.textContent = "Archive Sync";
  }

  // Only update modal details if modal elements are in DOM
  const modalBadge = document.getElementById("archive-modal-badge");
  const statTitles = document.getElementById("stat-db-titles");
  const statDbSize = document.getElementById("stat-db-size");
  const statLastSync = document.getElementById("stat-last-sync");
  const statLastSyncSub = document.getElementById("stat-last-sync-sub");
  const statProgress = document.getElementById("stat-sync-progress");
  const statProgressSub = document.getElementById("stat-sync-sub");

  if (statTitles && data.db_titles !== undefined) {
    statTitles.textContent = data.db_titles.toLocaleString();
  }
  if (statDbSize && data.db_size_mb !== undefined) {
    statDbSize.textContent = `${data.db_size_mb} MB`;
  }
  if (statLastSync && data.last_sync) {
    statLastSync.textContent = formatRelativeTime(data.last_sync.last_sync);
    if (statLastSyncSub) {
      statLastSyncSub.textContent = data.last_sync.status === "success" ? "✓ Up to date" : "Last sync aborted";
    }
  }

  if (modalBadge) {
    if (data.is_running) {
      modalBadge.textContent = data.status_message || "Active";
      modalBadge.className = "archive-status-pill running";
    } else {
      modalBadge.textContent = "Ready";
      modalBadge.className = "archive-status-pill ready";
    }
  }

  if (statProgress) {
    if (data.is_running) {
      statProgress.textContent = data.task_type.toUpperCase();
      if (statProgressSub) {
        const stats = data.stats || {};
        statProgressSub.textContent = `New: ${stats.new_titles || 0} • Rev: ${stats.revisions_updated || 0} (${data.elapsed_seconds || 0}s)`;
      }
    } else {
      statProgress.textContent = "Idle";
      if (statProgressSub) statProgressSub.textContent = "No task running";
    }
  }

  // Toggle action buttons
  const btnSync = document.getElementById("btn-start-sync");
  const btnPosters = document.getElementById("btn-start-posters");
  const btnVacuum = document.getElementById("btn-start-vacuum");
  const btnCancel = document.getElementById("btn-cancel-task");

  if (btnSync) btnSync.disabled = data.is_running;
  if (btnPosters) btnPosters.disabled = data.is_running;
  if (btnVacuum) btnVacuum.disabled = data.is_running;
  if (btnCancel) {
    btnCancel.style.display = data.is_running ? "inline-flex" : "none";
  }

  // Render log lines
  const consoleElem = document.getElementById("archive-console");
  if (consoleElem && Array.isArray(data.log_lines)) {
    if (data.log_lines.length !== lastLogLinesLength || data.is_running) {
      lastLogLinesLength = data.log_lines.length;
      if (data.log_lines.length === 0) {
        consoleElem.innerHTML = `<div class="console-line dim"><span class="console-ts">--:--:--</span> Ready. Click "Sync with DVDCompare" to check for new releases and revisions.</div>`;
      } else {
        const html = data.log_lines.map(line => {
          const styleClass = line.style ? ` style-${line.style}` : "";
          return `<div class="console-line${styleClass}"><span class="console-ts">[${line.ts}]</span> ${escapeHtml(line.msg)}</div>`;
        }).join("");
        consoleElem.innerHTML = html;
      }

      const autoscroll = document.getElementById("archive-autoscroll");
      if (autoscroll && autoscroll.checked) {
        consoleElem.scrollTop = consoleElem.scrollHeight;
      }
    }
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function clearArchiveLog() {
  const consoleElem = document.getElementById("archive-console");
  if (consoleElem) {
    consoleElem.innerHTML = `<div class="console-line dim"><span class="console-ts">--:--:--</span> Log cleared.</div>`;
  }
  lastLogLinesLength = 0;
}

async function triggerArchiveSync() {
  try {
    const res = await fetch("/api/archive/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (!data.ok) {
      alert(data.message || "Could not start sync task.");
    }
    pollArchiveStatus();
  } catch (err) {
    alert(`Failed to start sync: ${err.message}`);
  }
}

async function triggerPostersBackfill() {
  try {
    const res = await fetch("/api/archive/posters", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (!data.ok) {
      alert(data.message || "Could not start poster backfill.");
    }
    pollArchiveStatus();
  } catch (err) {
    alert(`Failed to start poster backfill: ${err.message}`);
  }
}

async function triggerDatabaseVacuum() {
  try {
    const res = await fetch("/api/archive/vacuum", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (!data.ok) {
      alert(data.message || "Could not start database optimization.");
    }
    pollArchiveStatus();
  } catch (err) {
    alert(`Failed to start optimization: ${err.message}`);
  }
}

async function cancelArchiveTask() {
  try {
    const res = await fetch("/api/archive/cancel", { method: "POST" });
    const data = await res.json();
    pollArchiveStatus();
  } catch (err) {
    console.error("Cancel error:", err);
  }
}

// Global initialization and Escape key listener
document.addEventListener("DOMContentLoaded", () => {
  pollArchiveStatus();
  startArchivePolling(20000);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isArchiveModalOpen) {
      closeArchiveModal();
    }
  });
});
