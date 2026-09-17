let columnChooserInstanceCounter = 0;

class ColumnChooser extends HTMLElement {
  constructor() {
    super();
    this.titleId = `column-chooser-title-${++columnChooserInstanceCounter}`;

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
  }

  connectedCallback() {
    if (this._modal) return;
    this.innerHTML = `
      <datasette-modal><dialog aria-labelledby="${this.titleId}">
          <div class="modal-header">
            <span class="modal-title" id="${this.titleId}">Choose columns</span>
            <span class="modal-meta"></span>
          </div>
          <div class="list-toolbar">
            <button class="select-all">Select all</button>
            <button class="deselect-all">Deselect all</button>
          </div>
          <div class="list-wrap">
            <div class="scroll-pulse top"></div>
            <div class="scroll-pulse bot"></div>
            <ul class="drag-list"></ul>
          </div>
          <div class="modal-footer">
            <span class="footer-info"></span>
            <button class="modal-btn modal-btn-ghost">Cancel</button>
            <button class="modal-btn modal-btn-primary">Apply</button>
          </div>
      </dialog></datasette-modal>
    `;

    // DOM refs
    this._modal = this.querySelector("datasette-modal");
    this._listWrap = this.querySelector(".list-wrap");
    this._dragList = this.querySelector(".drag-list");
    this._pulseTop = this.querySelector(".scroll-pulse.top");
    this._pulseBot = this.querySelector(".scroll-pulse.bot");
    this._selectAllBtn = this.querySelector(".select-all");
    this._deselectAllBtn = this.querySelector(".deselect-all");
    this._cancelBtn = this.querySelector(".modal-btn-ghost");
    this._applyBtn = this.querySelector(".modal-btn-primary");
    this._countEl = this.querySelector(".modal-meta");
    this._footerEl = this.querySelector(".footer-info");

    // Event listeners
    this._selectAllBtn.addEventListener("click", () => this._selectAll());
    this._deselectAllBtn.addEventListener("click", () => this._deselectAll());
    this._cancelBtn.addEventListener("click", () =>
      this._modal.requestClose("cancel"),
    );
    this._applyBtn.addEventListener("click", () => this._apply());
    this._modal.beforeClose = () => {
      this._items = this._savedItems ? [...this._savedItems] : this._items;
      this._checked = this._savedChecked
        ? new Set(this._savedChecked)
        : this._checked;
      return true;
    };
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
    this._modal.show();
  }

  // ── Internal methods ──

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
    this._modal.close();
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
          <span class="drag-item-label"></span>
        </label>
        <div class="drop-indicator"></div>
      `;

      li.querySelector(".drag-item-label").textContent = col;

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

    // Keep the drag preview inside the dialog so it stays above the backdrop.
    this._ghost = document.createElement("div");
    this._ghost.className = "drag-ghost";
    this._ghost.style.width = rect.width + "px";
    this._ghost.style.height = rect.height + "px";
    this._ghost.innerHTML = srcEl.innerHTML;
    this._ghost.querySelector(".drop-indicator")?.remove();
    const h = this._ghost.querySelector(".drag-handle");
    if (h) h.style.color = "var(--accent)";
    this._modal.dialog.appendChild(this._ghost);

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
