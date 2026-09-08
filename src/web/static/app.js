// DVDRewind Modern Client-side Application Logic (10-Point Blueprint)

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initSearch();
  initTableFilters();
  initAccordions();
  initCompareDock();
  initSynopsisToggle();
  initCutsToggle();
  initKeyboardShortcuts();
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
    toggleBtn.textContent = theme === "dark" ? "☀️ Light" : "🌙 Dark";
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
      const rowText = (row.textContent || "").toLowerCase();

      // Check pill filter condition
      let matchesPill = true;
      if (activeFilter === "us") {
        matchesPill = country.includes("united states") || country.includes("usa") || country.includes("america") || region.includes("r1") || region.includes("a");
      } else if (activeFilter === "uk") {
        matchesPill = country.includes("united kingdom") || country.includes("uk") || region.includes("r2") || region.includes("b");
      } else if (activeFilter === "free") {
        matchesPill = region.includes("free") || region.includes("all") || region.includes("abc") || region.includes("r0") || region.includes("uhd");
      } else if (activeFilter === "uncut") {
        matchesPill = !rowText.includes("cut") || rowText.includes("uncut") || rowText.includes("no cut");
      }

      // Check text search condition
      let matchesSearch = true;
      if (searchKeyword) {
        matchesSearch = rowText.includes(searchKeyword);
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

function initCutsToggle() {
  const toggleBtn = document.getElementById("btn-cuts-toggle");
  const drawer = document.getElementById("cuts-details-drawer");
  if (!toggleBtn || !drawer) return;

  toggleBtn.addEventListener("click", () => {
    const isHidden = drawer.style.display === "none" || !drawer.style.display;
    drawer.style.display = isHidden ? "block" : "none";
    toggleBtn.setAttribute("aria-expanded", isHidden ? "true" : "false");
    const arrow = toggleBtn.querySelector(".toggle-arrow");
    if (arrow) {
      arrow.textContent = isHidden ? "▴" : "▾";
    }
  });
}


