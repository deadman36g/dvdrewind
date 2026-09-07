// DVDRewind Modern Client-side Application Logic

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initSearch();
  initFilters();
  initCompareSelector();
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
    container.innerHTML = '<div style="padding: 0.75rem 1rem; color: var(--text-muted);">No comparisons found</div>';
    container.style.display = "block";
    return;
  }

  container.innerHTML = results.map(r => `
    <a href="/film/${r.fid}" class="search-dropdown-item">
      <div>
        <strong style="color: var(--text-primary);">${escapeHtml(r.clean_title)}</strong>
        <span style="color: var(--text-muted); font-size: 0.85rem;">(${r.year || 'N/A'})</span>
        ${r.overall_winner ? `<div style="font-size: 0.75rem; color: var(--accent-gold);">Winner: ${escapeHtml(r.overall_winner)}</div>` : ''}
      </div>
      <span class="badge badge-${badgeClass(r.format_category)}">${escapeHtml(r.format_category)}</span>
    </a>
  `).join("");
  container.style.display = "block";
}

// 3. Client-side Release Filtering
function initFilters() {
  const countrySelect = document.getElementById("filter-country");
  const regionSelect = document.getElementById("filter-region");
  const cutSelect = document.getElementById("filter-cut");
  const releaseCards = document.querySelectorAll(".release-card");

  if (!countrySelect && !regionSelect && !cutSelect) return;

  function applyFilters() {
    const country = countrySelect ? countrySelect.value : "";
    const region = regionSelect ? regionSelect.value : "";
    const cut = cutSelect ? cutSelect.value : "";

    let visibleCount = 0;
    releaseCards.forEach(card => {
      const cardCountry = card.getAttribute("data-country") || "";
      const cardRegion = card.getAttribute("data-region") || "";
      const cardCut = card.getAttribute("data-cut") || "";

      const matchCountry = !country || cardCountry === country;
      const matchRegion = !region || cardRegion.includes(region);
      const matchCut = !cut || cardCut === cut;

      if (matchCountry && matchRegion && matchCut) {
        card.style.display = "block";
        visibleCount++;
      } else {
        card.style.display = "none";
      }
    });

    const countElem = document.getElementById("filtered-releases-count");
    if (countElem) {
      countElem.textContent = visibleCount;
    }
  }

  [countrySelect, regionSelect, cutSelect].forEach(select => {
    if (select) select.addEventListener("change", applyFilters);
  });
}

// 4. Edition Comparison Selector (2 to 4 editions)
function initCompareSelector() {
  const checkboxes = document.querySelectorAll(".edition-compare-cb");
  const stickyBar = document.getElementById("compare-sticky-bar");
  const compareCountText = document.getElementById("compare-count-text");
  const compareBtn = document.getElementById("compare-action-btn");

  if (!checkboxes.length || !stickyBar) return;

  let selectedIndices = [];

  checkboxes.forEach(cb => {
    cb.addEventListener("change", () => {
      const idx = cb.getAttribute("data-index");
      if (cb.checked) {
        if (selectedIndices.length >= 4) {
          cb.checked = false;
          alert("You can compare up to 4 editions simultaneously.");
          return;
        }
        selectedIndices.push(idx);
      } else {
        selectedIndices = selectedIndices.filter(i => i !== idx);
      }

      if (selectedIndices.length >= 2) {
        stickyBar.style.display = "flex";
        compareCountText.textContent = `${selectedIndices.length} editions selected`;
      } else if (selectedIndices.length === 1) {
        stickyBar.style.display = "flex";
        compareCountText.textContent = "Select 1 more to compare";
      } else {
        stickyBar.style.display = "none";
      }
    });
  });

  if (compareBtn) {
    compareBtn.addEventListener("click", () => {
      if (selectedIndices.length >= 2) {
        const fid = compareBtn.getAttribute("data-fid");
        window.location.href = `/compare?fid=${fid}&indices=${selectedIndices.join(",")}`;
      }
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
