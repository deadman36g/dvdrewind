// DVDRewind Modern Client-side Application Logic (10-Point Blueprint)

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initSearch();
  initTableFilters();
  initTableSorting();
  initAccordions();
  initCompareDock();
  initSynopsisToggle();
  initHeroInlineShelf();
  initMasteringShelf();
  initArchiveShelf();
  initKeyboardShortcuts();
  initFixPosterButtons();
});

// 1. Dark/Light Theme Switcher
function initTheme() {
  const toggleBtn = document.getElementById("theme-toggle-btn");
  const currentTheme = localStorage.getItem("theme") || "dark";
  document.documentElement.setAttribute("data-theme", currentTheme);
  updateThemeIcon(currentTheme);

  if (toggleBtn) {
    toggleBtn.addEventListener("click", () => {
      const activeTheme = document.documentElement.getAttribute("data-theme");
      const nextTheme = activeTheme === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", nextTheme);
      localStorage.setItem("theme", nextTheme);
      updateThemeIcon(nextTheme);
    });
  }
}

function updateThemeIcon(theme) {
  const toggleBtn = document.getElementById("theme-toggle-btn");
  if (toggleBtn) {
    if (theme === "dark") {
      toggleBtn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="pointer-events: none; display: block;"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>';
      toggleBtn.title = "Switch theme";
      toggleBtn.setAttribute("aria-label", "Switch theme");
    } else {
      toggleBtn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="pointer-events: none; display: block;"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
      toggleBtn.title = "Switch theme";
      toggleBtn.setAttribute("aria-label", "Switch theme");
    }
  }
}

// 2. Instant Search & Live Dropdown
function initSearch() {
  const searchInput = document.getElementById("nav-search-input");
  const dropdown = document.getElementById("nav-search-dropdown");
  if (!searchInput || !dropdown) return;

  let debounceTimer = null;

  searchInput.addEventListener("input", (e) => {
    clearTimeout(debounceTimer);
    const query = e.target.value.trim();
    if (query.length < 2) {
      dropdown.style.display = "none";
      dropdown.innerHTML = "";
      return;
    }

    debounceTimer = setTimeout(async () => {
      try {
        const res = await fetch(`/api/search?q=${encodeURIComponent(query)}&limit=6`);
        if (!res.ok) return;
        const results = await res.json();
        renderSearchDropdown(results, dropdown);
      } catch (err) {
        console.error("Search error:", err);
      }
    }, 150);
  });

  document.addEventListener("click", (e) => {
    if (!searchInput.contains(e.target) && !dropdown.contains(e.target)) {
      dropdown.style.display = "none";
    }
  });
}

function renderSearchDropdown(results, container) {
  if (!results || results.length === 0) {
    container.innerHTML = '<div class="spotlight-empty">No matching film comparisons found</div>';
    container.style.display = "block";
    return;
  }

  container.innerHTML = results.map(r => `
    <a href="/film/${r.fid}" class="spotlight-item">
      <div class="spotlight-poster">
        ${r.poster_url ? `<img src="${r.poster_url}" alt="" class="spotlight-thumb">` : `<span class="spotlight-icon">🎬</span>`}
      </div>
      <div class="spotlight-info">
        <div class="spotlight-title-line">
          <strong class="spotlight-title">${escapeHtml(r.clean_title)}</strong>
          <span class="spotlight-year">(${r.year || 'N/A'})</span>
        </div>
        <div class="spotlight-meta-line">
          ${r.overall_winner ? `<span class="spotlight-winner">★ Winner: ${escapeHtml(r.overall_winner)}</span>` : ''}
          <span class="spotlight-count">${r.release_count || 1} edition${(r.release_count || 1) === 1 ? '' : 's'}</span>
        </div>
      </div>
      <span class="spotlight-badge badge-${badgeClass(r.format_category)}">${escapeHtml(r.format_category)}</span>
    </a>
  `).join("");
  container.style.display = "block";
}

// 3. Interactive Filter Pills & Live Table Search (Point 5)
function initTableFilters() {
  const pillButtons = document.querySelectorAll(".filter-pill");
  const searchInput = document.getElementById("editions-live-search");
  const clearBtn = document.getElementById("search-clear-btn");
  const summaryRows = document.querySelectorAll(".summary-row");

  if (!summaryRows.length) return;

  let activeFilter = "all";
  let searchKeyword = "";

  function applyFilters() {
    let visible = 0;
    summaryRows.forEach(row => {
      const idx = row.getAttribute("data-index");
      const accordionRow = document.getElementById(`accordion-row-${idx}`);
      const country = (row.getAttribute("data-country") || "").toLowerCase();
      const region = (row.getAttribute("data-region") || "").toLowerCase();
      const caseType = (row.getAttribute("data-case") || "").toLowerCase();
      const dist = (row.getAttribute("data-distributor") || "").toLowerCase();
      const fmt = (row.getAttribute("data-format") || "").toLowerCase();
      const rowText = (row.textContent || "").toLowerCase();

      // Check pill filter condition
      let matchesPill = true;
      const badgesStr = (row.getAttribute("data-badges") || "");
      const rowBadges = badgesStr.split(" ").filter(Boolean);

      if (activeFilter === "4k") {
        matchesPill = fmt.includes("4k") || fmt.includes("uhd");
      } else if (activeFilter === "bluray") {
        matchesPill = fmt.includes("blu");
      } else if (activeFilter === "dvd") {
        matchesPill = fmt.includes("dvd");
      } else if (activeFilter === "us") {
        matchesPill = country.includes("united states") || country.includes("usa") || country.includes("america") || region.includes("r1") || region.includes("a");
      } else if (activeFilter === "uk") {
        matchesPill = country.includes("united kingdom") || country.includes("uk") || region.includes("r2") || region.includes("b");
      } else if (activeFilter === "free") {
        matchesPill = region.includes("free") || region.includes("all") || region.includes("abc") || region.includes("r0") || region.includes("uhd");
      } else if (activeFilter === "uncut") {
        matchesPill = !rowText.includes("cut") || rowText.includes("uncut") || rowText.includes("no cut");
      } else if (activeFilter === "badge-purist") {
        matchesPill = rowBadges.includes("badge-purist") || rowBadges.includes("badge-mono");
      } else if (activeFilter.startsWith("badge-")) {
        matchesPill = rowBadges.includes(activeFilter);
      }

      // Check text search condition
      let matchesSearch = true;
      if (searchKeyword) {
        matchesSearch = rowText.includes(searchKeyword) || 
                        caseType.includes(searchKeyword) || 
                        dist.includes(searchKeyword) ||
                        country.includes(searchKeyword);
      }

      if (matchesPill && matchesSearch) {
        row.style.display = "";
        visible++;
      } else {
        row.style.display = "none";
        if (accordionRow) accordionRow.style.display = "none";
      }
    });

    const countElem = document.getElementById("filtered-releases-count");
    if (countElem) countElem.textContent = visible;
  }

  pillButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      pillButtons.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      activeFilter = btn.getAttribute("data-filter") || "all";
      applyFilters();
    });
  });

  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      searchKeyword = e.target.value.trim().toLowerCase();
      if (clearBtn) clearBtn.style.display = searchKeyword ? "block" : "none";
      applyFilters();
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      if (searchInput) searchInput.value = "";
      searchKeyword = "";
      clearBtn.style.display = "none";
      applyFilters();
    });
  }
}

// 4. Slide-Down Inline Accordions (Point 6)
function initAccordions() {
  const toggleButtons = document.querySelectorAll(".btn-toggle-accordion");
  if (!toggleButtons.length) return;

  toggleButtons.forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const targetId = btn.getAttribute("data-target");
      const targetRow = document.getElementById(targetId);
      if (!targetRow) return;

      const isVisible = targetRow.style.display !== "none";
      if (isVisible) {
        targetRow.style.display = "none";
        btn.classList.remove("open");
        const arrow = btn.querySelector(".accordion-arrow");
        if (arrow) arrow.innerHTML = "&darr;";
      } else {
        targetRow.style.display = "table-row";
        btn.classList.add("open");
        const arrow = btn.querySelector(".accordion-arrow");
        if (arrow) arrow.innerHTML = "&uarr;";
      }
    });
  });

  // Deep linking: auto-expand edition if #row-X or #accordion-row-X is in URL
  const hash = window.location.hash;
  if (hash) {
    const cleanId = hash.replace("#", "");
    const targetId = cleanId.startsWith("accordion-") ? cleanId : `accordion-${cleanId}`;
    const targetRow = document.getElementById(targetId);
    const btn = document.querySelector(`.btn-toggle-accordion[data-target="${targetId}"]`);
    if (targetRow && btn) {
      targetRow.style.display = "table-row";
      btn.classList.add("open");
      const arrow = btn.querySelector(".accordion-arrow");
      if (arrow) arrow.innerHTML = "&uarr;";
      setTimeout(() => {
        targetRow.scrollIntoView({ behavior: "smooth", block: "center" });
      }, 150);
    }
  }
}

// 5. Floating Bottom Compare Action Dock (Point 4)
function initCompareDock() {
  const checkboxes = document.querySelectorAll(".edition-compare-cb");
  const floatingDock = document.getElementById("compare-floating-dock");
  const countDisplay = document.getElementById("dock-count-display");
  const compareBtn = document.getElementById("dock-compare-btn");
  const clearBtn = document.getElementById("dock-clear-btn");
  const thumbnailsPreview = document.getElementById("dock-thumbnails-preview");

  if (!checkboxes.length || !floatingDock) return;

  let selected = []; // Array of { index, header, country }

  function updateDock() {
    if (selected.length > 0) {
      floatingDock.style.display = "flex";
      requestAnimationFrame(() => floatingDock.classList.add("dock-visible"));
      countDisplay.textContent = `${selected.length} edition${selected.length === 1 ? '' : 's'} selected (up to 4)`;

      thumbnailsPreview.innerHTML = selected.map(s => `
        <span class="dock-thumb-chip" title="${escapeHtml(s.header)}">
          #${s.index}
        </span>
      `).join("");

      if (compareBtn) {
        compareBtn.disabled = selected.length < 2;
        const btnSpan = compareBtn.querySelector("span");
        if (btnSpan) {
          btnSpan.textContent = selected.length < 2 ? "Select 1 more to compare" : `Compare ${selected.length} Editions`;
        }
      }
    } else {
      floatingDock.classList.remove("dock-visible");
      setTimeout(() => {
        if (selected.length === 0) floatingDock.style.display = "none";
      }, 200);
    }
  }

  checkboxes.forEach(cb => {
    cb.addEventListener("change", () => {
      const idx = cb.getAttribute("data-index");
      const header = cb.getAttribute("data-header") || `Edition #${idx}`;
      const country = cb.getAttribute("data-country") || "";

      if (cb.checked) {
        if (selected.length >= 4) {
          cb.checked = false;
          alert("You can compare up to 4 editions simultaneously.");
          return;
        }
        if (!selected.some(s => s.index === idx)) {
          selected.push({ index: idx, header, country });
        }
      } else {
        selected = selected.filter(s => s.index !== idx);
      }

      updateDock();
    });
  });

  if (compareBtn) {
    compareBtn.addEventListener("click", () => {
      if (selected.length >= 2) {
        const fid = compareBtn.getAttribute("data-fid");
        const indices = selected.map(s => s.index).join(",");
        window.location.href = `/compare?fid=${fid}&indices=${indices}`;
      }
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      checkboxes.forEach(cb => { cb.checked = false; });
      selected = [];
      updateDock();
    });
  }
}

// 5. Keyboard Shortcuts
function initKeyboardShortcuts() {
  document.addEventListener("keydown", (e) => {
    // Focus search on "/"
    if (e.key === "/" && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
      e.preventDefault();
      const searchInput = document.getElementById("nav-search-input") || document.getElementById("hero-search-input");
      if (searchInput) searchInput.focus();
    }
    // Close dropdown on Escape
    if (e.key === "Escape") {
      const dropdown = document.getElementById("nav-search-dropdown");
      if (dropdown) dropdown.style.display = "none";
    }
  });
}

// Utilities
function escapeHtml(str) {
  if (!str) return "";
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function badgeClass(fmt) {
  if (!fmt) return "dvd";
  const f = fmt.toLowerCase();
  if (f.includes("4k") || f.includes("uhd")) return "4k";
  if (f.includes("blu-ray") || f.includes("bd")) return "bluray";
  if (f.includes("hd dvd")) return "hddvd";
  return "dvd";
}

function initSynopsisToggle() {
  const toggleBtn = document.getElementById("btn-synopsis-toggle");
  const container = document.getElementById("hero-synopsis-container");
  if (!toggleBtn || !container) return;

  toggleBtn.addEventListener("click", () => {
    const isExpanded = container.classList.toggle("expanded");
    toggleBtn.textContent = isExpanded ? "Show less ▴" : "Read more ▾";
    toggleBtn.setAttribute("aria-expanded", isExpanded ? "true" : "false");
  });
}
// 7. Inline Collapsible Comparison Shelf (Verdict, Cuts, & Embedded Mobile Services)
function initHeroInlineShelf() {
  const shelf = document.getElementById("hero-inline-shelf");
  if (!shelf) return;

  const triggers = document.querySelectorAll(".btn-inline-drawer-trigger");
  const tabGroup = document.getElementById("inline-shelf-tabs");
  const serviceHeader = document.getElementById("inline-service-header");
  const serviceNameLabel = document.getElementById("service-name-label");
  const openExternalLink = document.getElementById("inline-embed-open-link");
  const reloadBtn = document.getElementById("inline-embed-reload-btn");
  const closeBtn = document.getElementById("btn-close-inline-shelf");
  const iframe = document.getElementById("inline-embed-frame");
  const panels = shelf.querySelectorAll(".inline-shelf-panel");
  const tabBtns = shelf.querySelectorAll(".inline-shelf-tab-btn");

  let currentActiveTrigger = null;

  function openInternalTab(tabKey) {
    if (tabGroup) tabGroup.style.display = "flex";
    if (serviceHeader) serviceHeader.style.display = "none";
    if (openExternalLink) openExternalLink.style.display = "none";
    if (reloadBtn) reloadBtn.style.display = "none";

    tabBtns.forEach(btn => {
      const isTarget = btn.id === `tab-btn-${tabKey}`;
      btn.classList.toggle("active", isTarget);
      btn.setAttribute("aria-selected", isTarget ? "true" : "false");
    });

    panels.forEach(panel => {
      const isTarget = panel.id === `inline-panel-${tabKey}`;
      panel.classList.toggle("active", isTarget);
      panel.style.display = isTarget ? "block" : "none";
    });

    shelf.style.display = "block";
    shelf.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function openEmbeddedService(trigger) {
    const embedUrl = trigger.dataset.embedUrl;
    const externalUrl = trigger.dataset.externalUrl || "#";
    const serviceName = trigger.dataset.serviceName || "External Resource";

    if (tabGroup) tabGroup.style.display = "none";
    if (serviceHeader) {
      serviceHeader.style.display = "flex";
      if (serviceNameLabel) serviceNameLabel.textContent = serviceName;
    }
    if (openExternalLink) {
      openExternalLink.href = externalUrl;
      openExternalLink.style.display = "inline-flex";
    }
    if (reloadBtn) {
      reloadBtn.style.display = "inline-flex";
    }

    panels.forEach(panel => {
      const isEmbedPanel = panel.id === "inline-panel-embed";
      panel.classList.toggle("active", isEmbedPanel);
      panel.style.display = isEmbedPanel ? "block" : "none";
    });

    if (iframe && embedUrl) {
      if (iframe.getAttribute("data-loaded-url") !== embedUrl) {
        iframe.src = embedUrl;
        iframe.setAttribute("data-loaded-url", embedUrl);
      }
    }

    shelf.style.display = "block";
    shelf.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function closeInlineShelf() {
    shelf.style.display = "none";
    triggers.forEach(trig => {
      trig.classList.remove("is-open");
      trig.setAttribute("aria-expanded", "false");
    });
    currentActiveTrigger = null;
  }

  triggers.forEach(trig => {
    trig.addEventListener("click", (e) => {
      e.preventDefault();

      const isSameActive = currentActiveTrigger === trig && shelf.style.display !== "none";
      if (isSameActive) {
        closeInlineShelf();
        return;
      }

      triggers.forEach(t => {
        t.classList.remove("is-open");
        t.setAttribute("aria-expanded", "false");
      });
      trig.classList.add("is-open");
      trig.setAttribute("aria-expanded", "true");
      currentActiveTrigger = trig;

      const tabKey = trig.dataset.shelfTab;
      if (tabKey) {
        openInternalTab(tabKey);
      } else if (trig.dataset.embedUrl) {
        openEmbeddedService(trig);
      }
    });
  });

  tabBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      const targetId = btn.dataset.target;
      if (targetId) {
        const tabKey = targetId.replace("inline-panel-", "");
        openInternalTab(tabKey);
        triggers.forEach(t => {
          const isCurrent = t.dataset.shelfTab === tabKey;
          t.classList.toggle("is-open", isCurrent);
          t.setAttribute("aria-expanded", isCurrent ? "true" : "false");
          if (isCurrent) currentActiveTrigger = t;
        });
      }
    });
  });

  if (reloadBtn) {
    reloadBtn.addEventListener("click", (e) => {
      e.preventDefault();
      if (iframe && iframe.src) {
        iframe.src = iframe.src;
      }
    });
  }

  if (closeBtn) {
    closeBtn.addEventListener("click", (e) => {
      e.preventDefault();
      closeInlineShelf();
    });
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && shelf.style.display !== "none") {
      closeInlineShelf();
    }
  });
}

// 8. Table Column Sorting (Sortable Categories: Country, Distributor, Year, Region, Transfer, Audio, Index)
function initTableSorting() {
  const table = document.getElementById("summary-table");
  if (!table) return;

  const tbody = table.querySelector("tbody");
  if (!tbody) return;

  const headers = table.querySelectorAll(".sortable-th[data-sort-col]");
  let activeCol = null;
  let activeDir = "none"; // 'asc', 'desc', 'none'

  function getRowPairs() {
    const summaryRows = Array.from(tbody.querySelectorAll("tr.summary-row"));
    return summaryRows.map(row => {
      const index = parseInt(row.dataset.index, 10) || 0;
      const drawerRow = document.getElementById(`drawer-${index}`);
      return {
        row,
        drawerRow,
        index,
        country: (row.dataset.country || "").trim().toLowerCase(),
        distributor: (row.dataset.distributor || "").trim().toLowerCase(),
        year: parseInt(row.dataset.year, 10) || 0,
        region: (row.dataset.region || "").trim().toLowerCase(),
        transfer: parseInt(row.dataset.transferRank, 10) || 3,
        audio: parseInt(row.dataset.audioRank, 10) || 3
      };
    });
  }

  function sortTable(col) {
    if (activeCol === col) {
      if (activeDir === "asc") activeDir = "desc";
      else if (activeDir === "desc") activeDir = "none";
      else activeDir = "asc";
    } else {
      activeCol = col;
      activeDir = "asc";
    }

    // Update indicators on all headers
    headers.forEach(th => {
      const ind = th.querySelector(".sort-indicator");
      if (th.dataset.sortCol === activeCol && activeDir !== "none") {
        th.classList.toggle("sort-asc", activeDir === "asc");
        th.classList.toggle("sort-desc", activeDir === "desc");
        if (ind) ind.textContent = activeDir === "asc" ? "▲" : "▼";
      } else {
        th.classList.remove("sort-asc", "sort-desc");
        if (ind) ind.textContent = "↕";
      }
    });

    const pairs = getRowPairs();

    if (activeDir === "none") {
      pairs.sort((a, b) => a.index - b.index);
    } else {
      const mult = activeDir === "asc" ? 1 : -1;
      pairs.sort((a, b) => {
        let cmp = 0;
        if (activeCol === "index") {
          cmp = a.index - b.index;
        } else if (activeCol === "year") {
          cmp = (a.year || 9999) - (b.year || 9999);
        } else if (activeCol === "transfer") {
          cmp = a.transfer - b.transfer;
        } else if (activeCol === "audio") {
          cmp = a.audio - b.audio;
        } else if (activeCol === "country") {
          cmp = a.country.localeCompare(b.country);
        } else if (activeCol === "distributor") {
          cmp = a.distributor.localeCompare(b.distributor);
        } else if (activeCol === "region") {
          cmp = a.region.localeCompare(b.region);
        }
        if (cmp === 0) {
          return a.index - b.index;
        }
        return cmp * mult;
      });
    }

    const fragment = document.createDocumentFragment();
    pairs.forEach(p => {
      fragment.appendChild(p.row);
      if (p.drawerRow) fragment.appendChild(p.drawerRow);
    });
    tbody.appendChild(fragment);
  }

  headers.forEach(th => {
    th.addEventListener("click", (e) => {
      // Don't sort if clicking on the '?' key button inside the header
      if (e.target.closest(".btn-trigger-mastering-key")) return;
      e.preventDefault();
      const col = th.dataset.sortCol;
      if (col) sortTable(col);
    });
  });
}

// 9. Archived Comparison Records Shelf
function initArchiveShelf() {
  const shelf = document.getElementById("archive-history-shelf");
  if (!shelf) return;

  const toggleBar = document.getElementById("archive-shelf-toggle-bar");
  const expandedBody = document.getElementById("archive-shelf-expanded-body");
  const togglePill = document.getElementById("archive-toggle-pill");

  function toggleArchiveShelf(forceOpen) {
    const isCurrentlyOpen = expandedBody.style.display !== "none";
    const shouldOpen = forceOpen !== undefined ? forceOpen : !isCurrentlyOpen;

    if (shouldOpen) {
      expandedBody.style.display = "block";
      toggleBar.setAttribute("aria-expanded", "true");
      if (togglePill) {
        togglePill.textContent = "▲ Hide Revision Log (Click to collapse)";
        togglePill.classList.add("expanded");
      }
    } else {
      expandedBody.style.display = "none";
      toggleBar.setAttribute("aria-expanded", "false");
      if (togglePill) {
        togglePill.textContent = "▼ Show Revision Log (Click to expand)";
        togglePill.classList.remove("expanded");
      }
    }
  }

  if (toggleBar) {
    toggleBar.addEventListener("click", () => {
      toggleArchiveShelf();
    });
    toggleBar.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggleArchiveShelf();
      }
    });
  }
}

// 8. Collector Mastering Reference Shelf & Table Header Links
function initMasteringShelf() {
  const shelf = document.getElementById("mastering-hierarchy-shelf");
  if (!shelf) return;

  const toggleBar = document.getElementById("shelf-toggle-bar");
  const expandedBody = document.getElementById("shelf-expanded-body");
  const togglePill = document.getElementById("shelf-toggle-pill");
  const navTabs = shelf.querySelectorAll(".shelf-nav-tab");
  const panels = shelf.querySelectorAll(".shelf-guide-panel");
  const keyTriggers = document.querySelectorAll(".btn-trigger-mastering-key");

  function toggleShelf(forceOpen) {
    const isCurrentlyOpen = expandedBody.style.display !== "none";
    const shouldOpen = forceOpen !== undefined ? forceOpen : !isCurrentlyOpen;

    if (shouldOpen) {
      expandedBody.style.display = "block";
      toggleBar.setAttribute("aria-expanded", "true");
      if (togglePill) {
        togglePill.textContent = "▲ Hide Guides (Click to collapse)";
        togglePill.classList.add("expanded");
      }
    } else {
      expandedBody.style.display = "none";
      toggleBar.setAttribute("aria-expanded", "false");
      if (togglePill) {
        togglePill.textContent = "▼ Show Guides (Click to expand)";
        togglePill.classList.remove("expanded");
      }
    }
  }

  function switchGuideTab(targetTabKey) {
    navTabs.forEach(tab => {
      const isTarget = tab.dataset.target === `guide-panel-${targetTabKey}`;
      tab.classList.toggle("active", isTarget);
      tab.setAttribute("aria-selected", isTarget ? "true" : "false");
    });

    panels.forEach(panel => {
      const isTarget = panel.id === `guide-panel-${targetTabKey}`;
      panel.classList.toggle("active", isTarget);
      panel.style.display = isTarget ? "block" : "none";
    });
  }

  if (toggleBar) {
    toggleBar.addEventListener("click", () => {
      toggleShelf();
    });
    toggleBar.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggleShelf();
      }
    });
  }

  navTabs.forEach(tab => {
    tab.addEventListener("click", () => {
      const targetId = tab.dataset.target;
      if (targetId) {
        const tabKey = targetId.replace("guide-panel-", "");
        switchGuideTab(tabKey);
      }
    });
  });

  keyTriggers.forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const tabKey = btn.dataset.tab || "video";
      toggleShelf(true);
      switchGuideTab(tabKey);
      shelf.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  });
}

// ========================================================
// 10. FIX POSTER MODAL LOGIC
// ========================================================
let pmCurrentFid = null;
let pmCurrentImdbId = null;
let pmSelectedUrl = null;
let pmSelectedFile = null;

function initFixPosterButtons() {
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".btn-fix-poster, #btn-fix-poster");
    if (btn) {
      e.preventDefault();
      e.stopPropagation();
      const fid = btn.dataset.fid || btn.getAttribute("data-fid");
      const title = btn.dataset.title || btn.getAttribute("data-title") || "";
      const year = btn.dataset.year || btn.getAttribute("data-year") || "";
      const poster = btn.dataset.poster || btn.getAttribute("data-poster") || "";
      const imdb = btn.dataset.imdb || btn.getAttribute("data-imdb") || "";
      if (fid) {
        openPosterModal(fid, title, year, poster, imdb);
      }
    }
  });
}

function openPosterModal(fid, title, year, currentUrl, imdbId) {
  pmCurrentFid = fid;
  pmCurrentImdbId = imdbId || null;
  pmSelectedUrl = null;
  pmSelectedFile = null;

  const modal = document.getElementById("poster-modal");
  if (!modal) return;

  const titleEl = document.getElementById("pm-title");
  const subtitleEl = document.getElementById("pm-subtitle");
  const searchInput = document.getElementById("pm-search-input");
  const urlInput = document.getElementById("pm-url-input");
  const fileInput = document.getElementById("pm-file-input");
  const saveBtn = document.getElementById("btn-save-poster");
  const feedback = document.getElementById("pm-feedback");

  if (titleEl) titleEl.textContent = `Change Poster — ${title} ${year ? '(' + year + ')' : ''}`;
  if (subtitleEl) subtitleEl.textContent = `Choose a poster for FID ${fid}. It will be cached permanently in your offline archive.`;
  if (searchInput) searchInput.value = `${title} ${year || ''}`.trim();
  if (urlInput) urlInput.value = "";
  if (fileInput) fileInput.value = "";
  if (saveBtn) {
    saveBtn.disabled = false;
    saveBtn.textContent = "Save Poster";
  }
  if (feedback) {
    feedback.className = "pm-feedback";
    feedback.style.display = "none";
    feedback.textContent = "";
  }

  updatePosterPreview(currentUrl || "");
  switchPosterTab("search");

  modal.style.display = "flex";
  document.body.style.overflow = "hidden";

  // Automatically trigger online search for candidates
  searchPosterCandidates();
}

function closePosterModal() {
  const modal = document.getElementById("poster-modal");
  if (modal) modal.style.display = "none";
  document.body.style.overflow = "";
}

function updatePosterPreview(url) {
  const img = document.getElementById("pm-preview-img");
  const empty = document.getElementById("pm-preview-empty");
  if (!img || !empty) return;

  if (url) {
    img.src = url;
    img.style.display = "block";
    empty.style.display = "none";
  } else {
    img.style.display = "none";
    empty.style.display = "block";
  }
}

function switchPosterTab(tabName) {
  const tabs = ["search", "url", "upload"];
  tabs.forEach(t => {
    const btn = document.getElementById(`tab-btn-${t}`);
    const pane = document.getElementById(`pm-tab-${t}`);
    if (btn) btn.classList.toggle("active", t === tabName);
    if (pane) pane.style.display = (t === tabName) ? "flex" : "none";
  });
}

async function searchPosterCandidates() {
  const searchInput = document.getElementById("pm-search-input");
  const statusEl = document.getElementById("pm-candidates-status");
  const gridEl = document.getElementById("pm-candidates-grid");
  const saveBtn = document.getElementById("btn-save-poster");
  if (!searchInput || !gridEl) return;

  const query = searchInput.value.trim();
  if (!query) return;

  if (statusEl) {
    statusEl.textContent = `Loading top official posters from TMDB...`;
    statusEl.style.display = "block";
  }
  gridEl.innerHTML = "";

  try {
    const res = await fetch(`/api/poster/search?query=${encodeURIComponent(query)}&fid=${pmCurrentFid || ''}&imdb_id=${encodeURIComponent(pmCurrentImdbId || '')}`);
    if (!res.ok) throw new Error("Search failed");
    const data = await res.json();
    const candidates = data.candidates || [];

    if (candidates.length === 0) {
      if (statusEl) statusEl.textContent = "No online poster candidates found. Try a different query, paste an image URL, or upload from disk.";
      return;
    }

    if (statusEl) statusEl.textContent = `Loaded top official posters from TMDB. Click to select, then click 'Save Poster':`;

    gridEl.innerHTML = candidates.map((c, idx) => `
      <div class="pm-candidate-card ${idx === 0 ? 'selected' : ''}" 
           data-url="${escapeHtml(c.url)}" 
           onclick="selectCandidatePoster('${escapeHtml(c.url)}', this)" 
           ondblclick="selectAndSaveCandidate('${escapeHtml(c.url)}', this)"
           title="Click to preview & select, or double-click to save immediately">
        <img src="${escapeHtml(c.thumb || c.url)}" alt="${escapeHtml(c.title)}" loading="lazy" onerror="this.parentElement.style.display='none'">
        <div class="pm-candidate-info" title="${escapeHtml(c.title)}">${escapeHtml(c.title)}</div>
      </div>
    `).join("");

    // Default select first candidate
    if (candidates.length > 0 && !pmSelectedUrl) {
      pmSelectedUrl = candidates[0].url;
      updatePosterPreview(candidates[0].url);
    }
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = "Save Selected Poster";
    }
  } catch (err) {
    if (statusEl) statusEl.textContent = "Error searching online candidates. You can still paste an image URL or upload a file.";
  }
}

function selectCandidatePoster(url, cardEl) {
  document.querySelectorAll(".pm-candidate-card").forEach(c => c.classList.remove("selected"));
  if (cardEl) cardEl.classList.add("selected");

  pmSelectedUrl = url;
  pmSelectedFile = null;
  updatePosterPreview(url);

  const saveBtn = document.getElementById("btn-save-poster");
  if (saveBtn) {
    saveBtn.disabled = false;
    saveBtn.textContent = "Save Selected Poster";
  }

  const feedback = document.getElementById("pm-feedback");
  if (feedback) {
    feedback.className = "pm-feedback";
    feedback.style.display = "block";
    feedback.textContent = "Poster selected! Click 'Save Selected Poster' to apply (or double-click card).";
  }
}

async function selectAndSaveCandidate(url, cardEl) {
  selectCandidatePoster(url, cardEl);
  await saveChosenPoster();
}

function previewCustomUrl() {
  const urlInput = document.getElementById("pm-url-input");
  if (!urlInput) return;
  const url = urlInput.value.trim();
  if (!url) return;

  pmSelectedUrl = url;
  pmSelectedFile = null;
  updatePosterPreview(url);

  const saveBtn = document.getElementById("btn-save-poster");
  if (saveBtn) {
    saveBtn.disabled = false;
    saveBtn.textContent = "Save Poster";
  }
}

function handlePosterFileSelect(event) {
  const file = event.target.files[0];
  if (!file) return;

  pmSelectedFile = file;
  pmSelectedUrl = null;

  const reader = new FileReader();
  reader.onload = (e) => {
    updatePosterPreview(e.target.result);
    const saveBtn = document.getElementById("btn-save-poster");
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = "Save Uploaded Poster";
    }
  };
  reader.readAsDataURL(file);
}

async function saveChosenPoster() {
  if (!pmCurrentFid) return;
  const saveBtn = document.getElementById("btn-save-poster");
  const feedback = document.getElementById("pm-feedback");

  // Check URL tab value if user typed directly
  const urlInput = document.getElementById("pm-url-input");
  const tabUrl = document.getElementById("pm-tab-url");
  if (tabUrl && tabUrl.style.display !== "none" && urlInput && urlInput.value.trim()) {
    pmSelectedUrl = urlInput.value.trim();
  }

  // Fallback to currently selected or first candidate card
  if (!pmSelectedUrl && !pmSelectedFile) {
    const selCard = document.querySelector(".pm-candidate-card.selected") || document.querySelector(".pm-candidate-card");
    if (selCard && selCard.dataset.url) {
      pmSelectedUrl = selCard.dataset.url;
    }
  }

  if (!pmSelectedUrl && !pmSelectedFile) {
    if (feedback) {
      feedback.className = "pm-feedback error";
      feedback.style.display = "block";
      feedback.textContent = "Please select a poster, paste an image URL, or choose a file first.";
    }
    return;
  }

  if (saveBtn) {
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving...";
  }
  if (feedback) {
    feedback.className = "pm-feedback";
    feedback.style.display = "block";
    feedback.textContent = "Downloading & caching poster in local archive...";
  }

  try {
    let res;
    if (pmSelectedFile) {
      const formData = new FormData();
      formData.append("poster_file", pmSelectedFile);
      res = await fetch(`/api/poster/${pmCurrentFid}`, {
        method: "POST",
        body: formData
      });
    } else if (pmSelectedUrl) {
      res = await fetch(`/api/poster/${pmCurrentFid}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ poster_url: pmSelectedUrl })
      });
    }

    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.error || "Failed to save poster");
    }

    if (feedback) {
      feedback.className = "pm-feedback success";
      feedback.textContent = "✓ Poster updated and cached successfully!";
    }

    // Update main poster image on current page dynamically
    const mainImg = document.getElementById("main-poster-img");
    const placeholder = document.getElementById("poster-placeholder");
    const wrapper = document.getElementById("poster-wrapper");
    const newPosterUrl = data.poster_url + (data.poster_url.includes("?") ? "&" : "?") + "t=" + Date.now();

    if (mainImg) {
      mainImg.src = newPosterUrl;
    } else if (wrapper) {
      if (placeholder) placeholder.remove();
      wrapper.innerHTML = `<img src="${newPosterUrl}" alt="Poster" class="movie-poster-img" id="main-poster-img">`;
    }

    // Update all elements holding data-poster
    document.querySelectorAll(`[data-fid="${pmCurrentFid}"]`).forEach(el => {
      el.setAttribute("data-poster", data.poster_url);
    });

    setTimeout(() => {
      closePosterModal();
    }, 700);
  } catch (err) {
    if (feedback) {
      feedback.className = "pm-feedback error";
      feedback.textContent = "Error: " + err.message;
    }
  } finally {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = "Save Poster";
    }
  }
}

async function autoDetectPoster() {
  if (!pmCurrentFid) return;
  const saveBtn = document.getElementById("btn-save-poster");
  const feedback = document.getElementById("pm-feedback");
  if (feedback) {
    feedback.className = "pm-feedback";
    feedback.style.display = "block";
    feedback.textContent = "Auto-detecting best poster from TMDB & Wikipedia...";
  }

  try {
    const res = await fetch(`/api/poster/${pmCurrentFid}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.error || "Could not auto-detect poster");
    }

    if (feedback) {
      feedback.className = "pm-feedback success";
      feedback.textContent = "✓ Auto-detected poster saved!";
    }

    const mainImg = document.getElementById("main-poster-img");
    const placeholder = document.getElementById("poster-placeholder");
    const wrapper = document.getElementById("poster-wrapper");
    const newPosterUrl = data.poster_url + (data.poster_url.includes("?") ? "&" : "?") + "t=" + Date.now();

    if (mainImg) {
      mainImg.src = newPosterUrl;
    } else if (wrapper) {
      if (placeholder) placeholder.remove();
      wrapper.innerHTML = `<img src="${newPosterUrl}" alt="Poster" class="movie-poster-img" id="main-poster-img">`;
    }

    document.querySelectorAll(`[data-fid="${pmCurrentFid}"]`).forEach(el => {
      el.setAttribute("data-poster", data.poster_url);
    });

    setTimeout(() => {
      closePosterModal();
    }, 700);
  } catch (err) {
    if (feedback) {
      feedback.className = "pm-feedback error";
      feedback.textContent = "Auto-detect failed: " + err.message;
    }
  }
}

// Close modal on Escape
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closePosterModal();
  }
});


