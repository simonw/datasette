class ColumnChooser extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });

    // State
    this._items = [];
    this._checked = new Set();
    this._savedItems = null;
    this._savedChecked = null;
    this._onApply = null;

    // Drag state
    this._ghost = null;
    this._dragSrcIdx = null;
    this._dropTargetIdx = null;
    this._dropPosition = null;
    this._ghostOffX = 0;
    this._ghostOffY = 0;
    this._autoScrollRAF = null;
    this._lastPointerY = 0;
    this._lastPointerX = 0;
    this._SCROLL_ZONE = 72;
    this._SCROLL_SPEED = 0.4;

    // Bound handlers
    this._onMove = this._onMove.bind(this);
    this._onUp = this._onUp.bind(this);

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          --ink: #0f0f0f;
          --paper: #f5f3ef;
          --muted: #6b6b6b;
          --rule: #e2dfd8;
          --accent: #1a56db;
          --accent-light: #e8effd;
          --card: #ffffff;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }

        dialog {
          border: none;
          border-radius: var(--modal-border-radius, 0.75rem);
          padding: 0;
          margin: auto;
          width: 100%;
          max-width: 420px;
          max-height: min(640px, calc(100vh - 32px));
          box-shadow: var(--modal-shadow, 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04));
          animation: slideIn var(--modal-animation-duration, 0.2s) ease-out;
          overflow: hidden;
          font-family: system-ui, -apple-system, sans-serif;
          background: var(--card);
          -webkit-user-select: none;
          -webkit-touch-callout: none;
          -webkit-tap-highlight-color: transparent;
        }

        dialog[open] {
          display: flex;
          flex-direction: column;
          height: min(640px, calc(100vh - 32px));
        }

        dialog::backdrop {
          background: var(--modal-backdrop-bg, rgba(0, 0, 0, 0.5));
          backdrop-filter: var(--modal-backdrop-blur, blur(4px));
          -webkit-backdrop-filter: var(--modal-backdrop-blur, blur(4px));
          animation: fadeIn var(--modal-animation-duration, 0.2s) ease-out;
        }

        @keyframes slideIn {
          from {
            opacity: 0;
            transform: translateY(-20px) scale(0.95);
          }
          to {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }

        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }

        .modal-header {
          padding: 20px 24px 16px;
          border-bottom: 1px solid var(--rule);
          display: flex;
          align-items: center;
          justify-content: space-between;
          flex-shrink: 0;
        }

        .modal-title {
          font-size: 1rem;
          font-weight: 600;
        }

        .modal-meta {
          font-family: ui-monospace, monospace;
          font-size: 0.7rem;
          color: var(--muted);
          background: var(--paper);
          padding: 3px 9px;
          border-radius: 20px;
        }

        .list-toolbar {
          padding: 6px 24px;
          border-bottom: 1px solid var(--rule);
          display: flex;
          gap: 12px;
          flex-shrink: 0;
        }

        .list-toolbar button {
          background: var(--accent-light);
          border: 1px solid var(--rule);
          border-radius: 4px;
          font-family: inherit;
          font-size: 0.75rem;
          color: var(--accent);
          cursor: pointer;
          padding: 3px 10px;
          transition: background 0.12s, color 0.12s;
        }
        .list-toolbar button:hover { background: var(--accent); color: white; }

        .list-wrap {
          flex: 1;
          overflow-y: auto;
          overflow-x: hidden;
          position: relative;
          overscroll-behavior: contain;
          -webkit-overflow-scrolling: touch;
        }

        .list-wrap::before,
        .list-wrap::after {
          content: '';
          position: sticky;
          display: block;
          left: 0; right: 0;
          height: 20px;
          pointer-events: none;
          z-index: 5;
          transition: opacity 0.2s;
        }
        .list-wrap::before {
          top: 0;
          background: linear-gradient(to bottom, rgba(255,255,255,0.9), transparent);
        }
        .list-wrap::after {
          bottom: 0;
          background: linear-gradient(to top, rgba(255,255,255,0.9), transparent);
          margin-top: -20px;
        }

        .scroll-zone {
          position: absolute;
          left: 0; right: 0;
          height: 72px;
          pointer-events: none;
          z-index: 10;
        }
        .scroll-zone-top { top: 0; }
        .scroll-zone-bot { bottom: 0; }

        .drag-list {
          list-style: none;
          padding: 4px 0;
        }

        .drag-item {
          display: flex;
          align-items: center;
          background: white;
          border-bottom: 1px solid var(--rule);
          user-select: none;
          -webkit-user-select: none;
          -webkit-touch-callout: none;
          position: relative;
          transition: background 0.08s;
        }

        .drag-item:last-child { border-bottom: none; }

        .drag-handle {
          display: flex;
          align-items: center;
          justify-content: center;
          width: 48px;
          height: 48px;
          flex-shrink: 0;
          cursor: grab;
          color: #c8c4bc;
          touch-action: none;
          transition: color 0.15s;
        }

        .drag-handle:hover { color: var(--accent); }
        .drag-handle svg { pointer-events: none; display: block; }

        .drag-item-content {
          display: flex;
          align-items: center;
          flex: 1;
          min-width: 0;
          cursor: pointer;
        }

        .drag-item-check {
          display: flex;
          align-items: center;
          width: 32px;
          height: 48px;
          flex-shrink: 0;
        }

        .drag-item-check input[type="checkbox"] {
          width: 16px;
          height: 16px;
          accent-color: var(--accent);
          cursor: pointer;
        }

        .drag-item-label {
          flex: 1;
          font-size: 0.9rem;
          line-height: 48px;
          padding-right: 16px;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          cursor: default;
        }

        .drag-item.is-dragging {
          opacity: 0;
        }

        .drop-indicator {
          position: absolute;
          left: 48px;
          right: 0;
          height: 2px;
          background: var(--accent);
          border-radius: 99px;
          pointer-events: none;
          z-index: 20;
          display: none;
        }
        .drop-indicator.top { top: -1px; display: block; }
        .drop-indicator.bottom { bottom: -1px; display: block; }

        .drag-ghost {
          position: fixed;
          pointer-events: none;
          z-index: 9999;
          background: white;
          border-radius: 6px;
          box-shadow: 0 8px 32px rgba(0,0,0,0.18), 0 2px 8px rgba(0,0,0,0.1);
          display: flex;
          align-items: center;
          border: 1.5px solid var(--accent-light);
          opacity: 0.97;
          will-change: transform;
          font-family: system-ui, -apple-system, sans-serif;
        }

        .scroll-pulse {
          position: absolute;
          left: 50%;
          transform: translateX(-50%);
          width: 32px;
          height: 32px;
          border-radius: 50%;
          background: var(--accent);
          opacity: 0;
          pointer-events: none;
          z-index: 10;
          transition: opacity 0.15s;
        }
        .scroll-pulse.top { top: 8px; }
        .scroll-pulse.bot { bottom: 8px; }
        .scroll-pulse.active {
          opacity: 0.18;
          animation: pulse 0.8s ease-in-out infinite;
        }

        @keyframes pulse {
          0%, 100% { transform: translateX(-50%) scale(1); opacity: 0.18; }
          50% { transform: translateX(-50%) scale(1.5); opacity: 0.07; }
        }

        .modal-footer {
          padding: 14px 20px;
          border-top: 1px solid var(--rule);
          display: flex;
          align-items: center;
          gap: 10px;
          flex-shrink: 0;
          background: var(--paper);
        }

        .footer-info {
          flex: 1;
          font-family: ui-monospace, monospace;
          font-size: 0.68rem;
          color: var(--muted);
        }

        .btn {
          border: none;
          border-radius: 5px;
          padding: 9px 20px;
          font-size: 0.85rem;
          font-weight: 500;
          cursor: pointer;
          touch-action: manipulation;
          font-family: inherit;
          transition: background 0.12s;
        }

        .btn-primary {
          background: var(--accent);
          color: white;
        }
        .btn-primary:hover { background: #1448c0; }

        .btn-ghost {
          background: transparent;
          color: var(--muted);
          border: 1px solid var(--rule);
        }
        .btn-ghost:hover { background: var(--rule); color: var(--ink); }

        .list-wrap::-webkit-scrollbar { width: 5px; }
        .list-wrap::-webkit-scrollbar-track { background: transparent; }
        .list-wrap::-webkit-scrollbar-thumb { background: var(--rule); border-radius: 99px; }

        input, textarea { -webkit-user-select: auto; user-select: auto; }
      </style>

      <dialog aria-labelledby="modalTitle">
          <div class="modal-header">
            <span class="modal-title" id="modalTitle">Choose columns</span>
            <span class="modal-meta" id="selectedCount"></span>
          </div>
          <div class="list-toolbar">
            <button id="selectAllBtn">Select all</button>
            <button id="deselectAllBtn">Deselect all</button>
          </div>
          <div class="list-wrap" id="listWrap">
            <div class="scroll-pulse top" id="pulseTop"></div>
            <div class="scroll-pulse bot" id="pulseBot"></div>
            <ul class="drag-list" id="dragList"></ul>
          </div>
          <div class="modal-footer">
            <span class="footer-info" id="footerInfo"></span>
            <button class="btn btn-ghost" id="cancelBtn">Cancel</button>
            <button class="btn btn-primary" id="applyBtn">Apply</button>
          </div>
      </dialog>
    `;

    // DOM refs
    this._dialog = this.shadowRoot.querySelector("dialog");
    this._listWrap = this.shadowRoot.getElementById("listWrap");
    this._dragList = this.shadowRoot.getElementById("dragList");
    this._pulseTop = this.shadowRoot.getElementById("pulseTop");
    this._pulseBot = this.shadowRoot.getElementById("pulseBot");
    this._selectAllBtn = this.shadowRoot.getElementById("selectAllBtn");
    this._deselectAllBtn = this.shadowRoot.getElementById("deselectAllBtn");
    this._cancelBtn = this.shadowRoot.getElementById("cancelBtn");
    this._applyBtn = this.shadowRoot.getElementById("applyBtn");
    this._countEl = this.shadowRoot.getElementById("selectedCount");
    this._footerEl = this.shadowRoot.getElementById("footerInfo");

    // Event listeners
    this._selectAllBtn.addEventListener("click", () => this._selectAll());
    this._deselectAllBtn.addEventListener("click", () => this._deselectAll());
    this._cancelBtn.addEventListener("click", () => this._close());
    this._applyBtn.addEventListener("click", () => this._apply());
    this._dialog.addEventListener("click", (e) => {
      if (e.target === this._dialog) this._close();
    });
    this._dialog.addEventListener("cancel", (e) => {
      e.preventDefault();
      this._close();
    });
  }

  /**
   * Open the column chooser dialog.
   * @param {Object} opts
   * @param {string[]} opts.columns - All available column names, in display order.
   * @param {string[]} opts.selected - Column names that should be pre-checked.
   * @param {function(string[]): void} opts.onApply - Called with the selected columns in order when Apply is clicked.
   */
  open({ columns, selected = [], onApply }) {
    this._items = [...columns];
    this._checked = new Set(selected);
    this._onApply = onApply || null;

    // Save state for cancel/restore
    this._savedItems = [...this._items];
    this._savedChecked = new Set(this._checked);

    this._render();
    this._dialog.showModal();
  }

  // ── Internal methods ──

  _close() {
    this._items = this._savedItems ? [...this._savedItems] : this._items;
    this._checked = this._savedChecked
      ? new Set(this._savedChecked)
      : this._checked;
    this._dialog.close();
  }

  _selectAll() {
    this._items.forEach((col) => this._checked.add(col));
    this._dragList.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      cb.checked = true;
    });
    this._updateCounts();
  }

  _deselectAll() {
    this._checked.clear();
    this._dragList.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      cb.checked = false;
    });
    this._updateCounts();
  }

  _apply() {
    const selected = this._items.filter((col) => this._checked.has(col));
    this._dialog.close();
    if (this._onApply) {
      this._onApply(selected);
    }
  }

  _render() {
    this._dragList.innerHTML = "";
    this._items.forEach((col, i) => {
      const li = document.createElement("li");
      li.className = "drag-item";
      li.dataset.idx = i;
      li.innerHTML = `
        <span class="drag-handle" aria-label="Drag to reorder">
          <svg width="12" height="18" viewBox="0 0 12 18" fill="currentColor">
            <circle cx="3.5" cy="3.5" r="1.8"/>
            <circle cx="8.5" cy="3.5" r="1.8"/>
            <circle cx="3.5" cy="9" r="1.8"/>
            <circle cx="8.5" cy="9" r="1.8"/>
            <circle cx="3.5" cy="14.5" r="1.8"/>
            <circle cx="8.5" cy="14.5" r="1.8"/>
          </svg>
        </span>
        <label class="drag-item-content">
          <span class="drag-item-check">
            <input type="checkbox" ${this._checked.has(col) ? "checked" : ""}>
          </span>
          <span class="drag-item-label">${col}</span>
        </label>
        <div class="drop-indicator"></div>
      `;

      li.querySelector("input").addEventListener("change", (e) => {
        e.target.checked ? this._checked.add(col) : this._checked.delete(col);
        this._updateCounts();
      });

      li.querySelector(".drag-handle").addEventListener("pointerdown", (e) =>
        this._startDrag(e, i),
      );
      this._dragList.appendChild(li);
    });

    this._updateCounts();
  }

  _updateCounts() {
    const n = this._checked.size;
    this._countEl.textContent = `${n} of ${this._items.length} selected`;
    this._footerEl.textContent = `${this._items.length} columns`;
  }

  // ── Drag engine ──

  _startDrag(e, idx) {
    e.preventDefault();
    this._dragSrcIdx = idx;

    const srcEl = this._dragList.children[idx];
    const rect = srcEl.getBoundingClientRect();

    this._ghostOffX = e.clientX - rect.left;
    this._ghostOffY = e.clientY - rect.top;

    // Build ghost inside shadow DOM
    this._ghost = document.createElement("div");
    this._ghost.className = "drag-ghost";
    this._ghost.style.width = rect.width + "px";
    this._ghost.style.height = rect.height + "px";
    this._ghost.innerHTML = srcEl.innerHTML;
    this._ghost.querySelector(".drop-indicator")?.remove();
    const h = this._ghost.querySelector(".drag-handle");
    if (h) h.style.color = "var(--accent)";
    this.shadowRoot.appendChild(this._ghost);

    srcEl.classList.add("is-dragging");
    this._positionGhost(e.clientX, e.clientY);

    document.addEventListener("pointermove", this._onMove);
    document.addEventListener("pointerup", this._onUp);
    document.addEventListener("pointercancel", this._onUp);
  }

  _positionGhost(cx, cy) {
    this._ghost.style.left = cx - this._ghostOffX + "px";
    this._ghost.style.top = cy - this._ghostOffY + "px";
  }

  _onMove(e) {
    this._lastPointerX = e.clientX;
    this._lastPointerY = e.clientY;
    this._positionGhost(e.clientX, e.clientY);
    this._updateDropTarget(e.clientY);
    this._updateAutoScroll(e.clientY);
  }

  _onUp() {
    document.removeEventListener("pointermove", this._onMove);
    document.removeEventListener("pointerup", this._onUp);
    document.removeEventListener("pointercancel", this._onUp);

    this._stopAutoScroll();

    const noMove =
      this._dropTargetIdx === null || this._dropTargetIdx === this._dragSrcIdx;
    this._clearDropIndicators();

    let dest = null;
    if (!noMove) {
      const moved = this._items.splice(this._dragSrcIdx, 1)[0];
      dest = this._dropTargetIdx;
      if (this._dropPosition === "after") dest++;
      if (dest > this._dragSrcIdx) dest--;
      this._items.splice(dest, 0, moved);
    }

    this._dragSrcIdx = null;
    this._dropTargetIdx = null;
    this._dropPosition = null;

    const g = this._ghost;
    this._ghost = null;

    if (noMove) {
      if (g) g.remove();
      this._render();
      return;
    }

    this._render();

    if (g && dest !== null) {
      const landedEl = this._dragList.children[dest];
      if (landedEl) {
        landedEl.style.opacity = "0";
        const r = landedEl.getBoundingClientRect();
        g.getBoundingClientRect();
        g.style.transition =
          "left 0.15s cubic-bezier(0.22, 1, 0.36, 1), top 0.15s cubic-bezier(0.22, 1, 0.36, 1), box-shadow 0.15s, opacity 0.1s 0.1s";
        g.style.left = r.left + "px";
        g.style.top = r.top + "px";
        g.style.boxShadow = "0 1px 4px rgba(0,0,0,0.08)";
        g.style.opacity = "0";
        setTimeout(() => {
          g.remove();
          if (landedEl) landedEl.style.opacity = "";
        }, 160);
      } else {
        g.remove();
      }
    } else if (g) {
      g.remove();
    }
  }

  _updateDropTarget(clientY) {
    this._clearDropIndicators();
    const listItems = [
      ...this._dragList.querySelectorAll(".drag-item:not(.is-dragging)"),
    ];
    if (!listItems.length) return;

    let best = null,
      bestDist = Infinity;
    listItems.forEach((li) => {
      const r = li.getBoundingClientRect();
      const mid = r.top + r.height / 2;
      const dist = Math.abs(clientY - mid);
      if (dist < bestDist) {
        bestDist = dist;
        best = li;
      }
    });

    if (!best) return;
    const r = best.getBoundingClientRect();
    const mid = r.top + r.height / 2;
    const above = clientY < mid;
    const indic = best.querySelector(".drop-indicator");

    this._dropTargetIdx = parseInt(best.dataset.idx);
    this._dropPosition = above ? "before" : "after";

    if (indic) {
      indic.className = "drop-indicator " + (above ? "top" : "bottom");
    }
  }

  _clearDropIndicators() {
    this._dragList.querySelectorAll(".drop-indicator").forEach((el) => {
      el.className = "drop-indicator";
    });
  }

  _updateAutoScroll(clientY) {
    const rect = this._listWrap.getBoundingClientRect();
    const relY = clientY - rect.top;
    const distTop = relY;
    const distBot = rect.height - relY;

    const inTop = distTop < this._SCROLL_ZONE && distTop >= 0;
    const inBot = distBot < this._SCROLL_ZONE && distBot >= 0;

    this._pulseTop.classList.toggle("active", inTop);
    this._pulseBot.classList.toggle("active", inBot);

    if ((inTop || inBot) && !this._autoScrollRAF) {
      let lastTime = null;
      const loop = (ts) => {
        if (!this._ghost) {
          this._stopAutoScroll();
          return;
        }
        if (lastTime !== null) {
          const dt = ts - lastTime;
          const rect2 = this._listWrap.getBoundingClientRect();
          const relY2 = this._lastPointerY - rect2.top;
          const dTop = relY2;
          const dBot = rect2.height - relY2;

          if (dTop < this._SCROLL_ZONE && dTop >= 0) {
            const factor = 1 - dTop / this._SCROLL_ZONE;
            this._listWrap.scrollTop -= this._SCROLL_SPEED * dt * factor * 2.5;
          } else if (dBot < this._SCROLL_ZONE && dBot >= 0) {
            const factor = 1 - dBot / this._SCROLL_ZONE;
            this._listWrap.scrollTop += this._SCROLL_SPEED * dt * factor * 2.5;
          } else {
            this._stopAutoScroll();
            return;
          }
          this._updateDropTarget(this._lastPointerY);
        }
        lastTime = ts;
        this._autoScrollRAF = requestAnimationFrame(loop);
      };
      this._autoScrollRAF = requestAnimationFrame(loop);
    }

    if (!inTop && !inBot) this._stopAutoScroll();
  }

  _stopAutoScroll() {
    if (this._autoScrollRAF) {
      cancelAnimationFrame(this._autoScrollRAF);
      this._autoScrollRAF = null;
    }
    this._pulseTop.classList.remove("active");
    this._pulseBot.classList.remove("active");
  }
}

customElements.define("column-chooser", ColumnChooser);
